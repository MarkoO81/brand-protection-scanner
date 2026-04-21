"""
Celery tasks: domain scanning, brand sweeps, periodic maintenance.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import aiohttp
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
    Core async scanning logic shared by all task types.
    Returns None if the domain does not resolve (unregistered / NXDOMAIN) —
    callers must check for None and skip persistence.
    """
    url = f"https://{domain}"

    # --- DNS resolution: gate on this before any expensive work ---
    ip = await resolve_domain(domain)
    if ip is None:
        logger.debug(f"NXDOMAIN — skipping {domain}")
        return None

    # --- Domain signals (cheap, run in parallel with DNS already done) ---
    sim = domain_similarity(domain, brand_domain)
    loop = asyncio.get_event_loop()
    whois_data, homoglyphs = await asyncio.gather(
        loop.run_in_executor(None, enrich_whois, domain),
        loop.run_in_executor(None, has_homoglyphs, domain),
    )

    # --- IP reputation + visual + content in parallel ---
    ip_task       = get_ip_reputation(ip)
    screenshot_task = take_screenshot(url, domain)
    html_task     = _fetch_html(url)

    ip_data, screenshot_path, html = await asyncio.gather(
        ip_task, screenshot_task, html_task
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
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from core.models import ScanResult

    engine = create_async_engine(settings.database_url, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        scan = ScanResult(**result)
        session.add(scan)
        await session.commit()
    await engine.dispose()


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
    """Fully analyse a single suspicious domain against a brand."""
    logger.info(f"Scanning {domain} vs {brand_domain}")
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
        logger.error(f"scan_domain failed: {exc}")
        raise self.retry(exc=exc)


@app.task(bind=True, max_retries=1, queue="discovery")
def sweep_brand(
    self,
    brand_domain: str,
    brand_keywords: list[str] | None = None,
    brand_phash: str | None = None,
    brand_palette: list | None = None,
) -> dict:
    """Generate permutations and enqueue a scan task for each live domain."""
    logger.info(f"Sweeping brand {brand_domain}")
    candidates = generate_permutations(brand_domain)
    logger.info(f"Generated {len(candidates)} candidates for {brand_domain}")

    queued = 0
    for candidate in candidates:
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

    return {"brand_domain": brand_domain, "candidates_queued": queued}


@app.task(queue="discovery")
def periodic_sweep() -> dict:
    """Triggered by Celery Beat — re-sweeps all registered brands."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
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
