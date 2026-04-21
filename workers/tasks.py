"""
Celery tasks: domain scanning, brand sweeps, periodic maintenance.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import aiohttp
import redis as sync_redis
from celery.utils.log import get_task_logger

from analyzers.content import analyze_content
from analyzers.domain import (
    domain_similarity,
    enrich_whois,
    generate_permutations,
    has_homoglyphs,
    resolve_domain,
)
from analyzers.ip_reputation import get_ip_reputation
from analyzers.scoring import Signals, compute_score
from analyzers.visual import (
    compute_phash,
    extract_palette,
    favicon_match,
    palette_similarity,
    phash_similarity,
    take_screenshot,
)
from core.config import settings
from workers.pipeline import app

logger = get_task_logger(__name__)

# How long (seconds) a scan lock is held.
# Covers the worst-case full scan duration + retry delays.
SCAN_LOCK_TTL = 600  # 10 minutes


# ---------------------------------------------------------------------------
# Deduplication lock helpers (synchronous Redis — used inside Celery tasks)
# ---------------------------------------------------------------------------

def _lock_key(domain: str, brand_domain: str) -> str:
    return f"scan:lock:{domain}:{brand_domain}"


def _acquire_lock(domain: str, brand_domain: str, task_id: str) -> bool:
    """
    Atomically set the lock key only if it does not already exist (NX).
    Returns True if the lock was acquired, False if already held.
    """
    r = sync_redis.from_url(settings.redis_url, decode_responses=True)
    acquired = r.set(_lock_key(domain, brand_domain), task_id, nx=True, ex=SCAN_LOCK_TTL)
    r.close()
    return bool(acquired)


def _release_lock(domain: str, brand_domain: str, task_id: str) -> None:
    """Release the lock only if it is still owned by this task (guard against stale releases)."""
    r = sync_redis.from_url(settings.redis_url, decode_responses=True)
    key = _lock_key(domain, brand_domain)
    if r.get(key) == task_id:
        r.delete(key)
    r.close()


def _is_locked(domain: str, brand_domain: str) -> bool:
    """Non-destructive check — used by sweep to skip already-queued domains."""
    r = sync_redis.from_url(settings.redis_url, decode_responses=True)
    exists = r.exists(_lock_key(domain, brand_domain))
    r.close()
    return bool(exists)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run(coro):
    """Run an async coroutine from a synchronous Celery task."""
    return asyncio.get_event_loop().run_until_complete(coro)


async def _fetch_html(url: str) -> Optional[str]:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, allow_redirects=True) as resp:
                return await resp.text(errors="replace")
    except Exception:
        return None


async def _full_scan(
    domain: str,
    brand_domain: str,
    brand_keywords: list[str],
    brand_phash: Optional[str],
    brand_palette: Optional[list],
    source: str = "manual",
) -> dict[str, Any] | None:
    """
    Core async scanning logic.
    Returns None if the domain does not resolve (NXDOMAIN).
    """
    url = f"https://{domain}"

    # --- DNS resolution: gate before any expensive work ---
    ip = await resolve_domain(domain)
    if ip is None:
        logger.debug(f"NXDOMAIN — skipping {domain}")
        return None

    # --- Cheap domain signals ---
    sim = domain_similarity(domain, brand_domain)
    loop = asyncio.get_event_loop()
    whois_data, homoglyphs = await asyncio.gather(
        loop.run_in_executor(None, enrich_whois, domain),
        loop.run_in_executor(None, has_homoglyphs, domain),
    )

    # --- Expensive I/O in parallel ---
    ip_data, screenshot_path, html = await asyncio.gather(
        get_ip_reputation(ip),
        take_screenshot(url, domain),
        _fetch_html(url),
    )

    # --- Visual signals ---
    candidate_phash = compute_phash(screenshot_path) if screenshot_path else None
    logo_sim = 0.0
    if brand_phash and candidate_phash:
        logo_sim = phash_similarity(brand_phash, candidate_phash)

    fav_match = await favicon_match(brand_domain, domain)

    candidate_palette = extract_palette(screenshot_path) if screenshot_path else []
    color_sim = palette_similarity(brand_palette or [], candidate_palette)

    # --- Content signals ---
    content_signals: dict = {}
    if html:
        content_signals = analyze_content(html, domain)

    # --- Score ---
    signals = Signals(
        similarity_score=sim,
        is_fresh_registration=whois_data.get("is_fresh", False),
        is_very_fresh=whois_data.get("is_very_fresh", False),
        has_homoglyphs=homoglyphs,
        logo_similarity=logo_sim,
        favicon_match=fav_match,
        color_similarity=color_sim,
        has_login_form=content_signals.get("has_login_form", False),
        has_external_form_action=content_signals.get("has_external_form_action", False),
        has_urgency_language=content_signals.get("has_urgency_language", False),
        has_impersonation_language=content_signals.get("has_impersonation_language", False),
        ip_reputation_score=ip_data.get("reputation_score", 0.0),
        bad_hosting_asn=ip_data.get("is_bad_asn", False),
    )
    score, verdict, breakdown = compute_score(signals)

    return {
        "domain": domain,
        "brand_domain": brand_domain,
        "score": score,
        "verdict": verdict,
        "signals": breakdown,
        "similarity_score": sim,
        "is_fresh_registration": signals.is_fresh_registration,
        "registration_days": whois_data.get("registration_days"),
        "has_homoglyphs": homoglyphs,
        "whois_data": whois_data,
        "logo_similarity": logo_sim,
        "favicon_match": fav_match,
        "color_similarity": color_sim,
        "screenshot_path": screenshot_path,
        **content_signals,
        "ip_address": ip,
        "ip_reputation_score": signals.ip_reputation_score,
        "bad_hosting_asn": signals.bad_hosting_asn,
        "source": source,
    }


async def _save_result(result: dict) -> None:
    """Persist scan result to PostgreSQL."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from core.models import ScanResult

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        scan = ScanResult(**result)
        session.add(scan)
        await session.commit()
    await engine.dispose()


# ---------------------------------------------------------------------------
# Celery tasks
# ---------------------------------------------------------------------------

@app.task(bind=True, max_retries=2, default_retry_delay=30, queue="scans")
def scan_domain(
    self,
    domain: str,
    brand_domain: str,
    brand_keywords: list[str] | None = None,
    brand_phash: str | None = None,
    brand_palette: list | None = None,
    source: str = "manual",
) -> dict:
    """
    Fully analyse a single suspicious domain against a brand.

    A Redis lock prevents duplicate concurrent scans for the same
    (domain, brand_domain) pair. If the lock is already held the task
    exits immediately without doing any work.
    """
    task_id = self.request.id

    # --- Deduplication check ---
    if not _acquire_lock(domain, brand_domain, task_id):
        logger.info(f"Duplicate — {domain} vs {brand_domain} already in progress, skipping")
        return {"domain": domain, "skipped": True, "reason": "duplicate"}

    logger.info(f"Scanning {domain} vs {brand_domain} [task={task_id}]")
    try:
        result = _run(
            _full_scan(
                domain=domain,
                brand_domain=brand_domain,
                brand_keywords=brand_keywords or [],
                brand_phash=brand_phash,
                brand_palette=brand_palette,
                source=source,
            )
        )
        if result is None:
            logger.info(f"Skipped {domain} — no DNS record")
            return {"domain": domain, "skipped": True, "reason": "NXDOMAIN"}

        _run(_save_result(result))
        return result

    except Exception as exc:
        logger.error(f"scan_domain failed for {domain}: {exc}")
        raise self.retry(exc=exc)

    finally:
        # Always release lock — even on failure / retry the next attempt
        # will re-acquire. On retry self.request.id stays the same so the
        # lock ownership check in _release_lock still passes.
        _release_lock(domain, brand_domain, task_id)


@app.task(bind=True, max_retries=1, queue="discovery")
def sweep_brand(
    self,
    brand_domain: str,
    brand_keywords: list[str] | None = None,
    brand_phash: str | None = None,
    brand_palette: list | None = None,
) -> dict:
    """
    Generate permutations and enqueue a scan task for each candidate.
    Skips candidates that already have an active scan lock in Redis.
    """
    logger.info(f"Sweeping brand {brand_domain}")
    candidates = generate_permutations(brand_domain)
    logger.info(f"Generated {len(candidates)} candidates for {brand_domain}")

    queued = 0
    skipped = 0
    for candidate in candidates:
        if _is_locked(candidate, brand_domain):
            logger.debug(f"Sweep skip — {candidate} already locked")
            skipped += 1
            continue

        scan_domain.apply_async(
            kwargs={
                "domain": candidate,
                "brand_domain": brand_domain,
                "brand_keywords": brand_keywords or [],
                "brand_phash": brand_phash,
                "brand_palette": brand_palette,
                "source": "sweep",
            }
        )
        queued += 1

    logger.info(f"Sweep done for {brand_domain}: {queued} queued, {skipped} skipped (already in progress)")
    return {"brand_domain": brand_domain, "candidates_queued": queued, "candidates_skipped": skipped}


@app.task(queue="discovery")
def periodic_sweep() -> dict:
    """Triggered by Celery Beat — re-sweeps all registered brands."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from core.models import Brand

    async def _get_brands():
        engine = create_async_engine(settings.database_url, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        async with session_factory() as session:
            result = await session.execute(select(Brand))
            brands = result.scalars().all()
        await engine.dispose()
        return brands

    brands = _run(_get_brands())
    dispatched = 0
    for brand in brands:
        sweep_brand.apply_async(
            kwargs={
                "brand_domain": brand.domain,
                "brand_keywords": brand.keywords,
                "brand_phash": brand.logo_phash,
                "brand_palette": brand.color_palette,
            }
        )
        dispatched += 1

    return {"brands_swept": dispatched}
