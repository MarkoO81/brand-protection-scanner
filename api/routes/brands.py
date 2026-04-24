"""POST /brands/fingerprint — capture brand visual fingerprint."""
from __future__ import annotations

import os
from typing import Optional

import aiohttp
import tldextract
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from analyzers.domain import resolve_domain
from analyzers.visual import (
    compute_phash,
    extract_palette,
    fetch_favicon,
    hash_favicon,
    take_screenshot,
)
from api.deps import get_db
from api.models import FingerprintRequest, FingerprintResponse
from core.config import settings
from core.models import Brand, ScanResult

router = APIRouter(prefix="/brands", tags=["brands"])


async def _domain_has_web_content(url: str) -> bool:
    """
    Quick HTTP check — returns True if the server responds with any HTTP status.
    A timeout or connection error means the site is not reachable.
    """
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=8)
        ) as session:
            async with session.get(url, allow_redirects=True) as resp:
                return resp.status < 600  # any real HTTP response counts
    except Exception:
        return False


@router.post("/fingerprint", response_model=FingerprintResponse)
async def fingerprint_brand(
    body: FingerprintRequest,
    db: AsyncSession = Depends(get_db),
):
    url = body.brand_url
    extracted = tldextract.extract(url)
    domain = f"{extracted.domain}.{extracted.suffix}"

    # ── Validation: DNS ──────────────────────────────────────────────────────
    ip = await resolve_domain(domain)
    if ip is None:
        raise HTTPException(
            status_code=422,
            detail=f"Domain '{domain}' does not resolve. Check that it is registered and spelled correctly.",
        )

    # ── Validation: live website ─────────────────────────────────────────────
    reachable = await _domain_has_web_content(url)
    if not reachable:
        raise HTTPException(
            status_code=422,
            detail=f"Domain '{domain}' resolves but no website was found. The site may be down or not serving HTTP.",
        )

    # Screenshot
    screenshot_path = await take_screenshot(url, domain)
    logo_phash = compute_phash(screenshot_path) if screenshot_path else None
    color_palette = extract_palette(screenshot_path) if screenshot_path else None

    # Favicon
    favicon_data = await fetch_favicon(domain)
    favicon_hash = hash_favicon(favicon_data) if favicon_data else None

    screenshot_url: Optional[str] = None
    if screenshot_path:
        filename = os.path.basename(screenshot_path)
        screenshot_url = f"{settings.screenshot_base_url}/{filename}"

    # Upsert brand record
    existing = await db.execute(select(Brand).where(Brand.domain == domain))
    brand = existing.scalar_one_or_none()

    if brand:
        brand.logo_phash = logo_phash
        brand.favicon_hash = favicon_hash
        brand.color_palette = [list(c) for c in color_palette] if color_palette else None
        brand.screenshot_path = screenshot_path
    else:
        brand = Brand(
            domain=domain,
            logo_phash=logo_phash,
            favicon_hash=favicon_hash,
            color_palette=[list(c) for c in color_palette] if color_palette else None,
            screenshot_path=screenshot_path,
        )
        db.add(brand)

    await db.commit()

    return FingerprintResponse(
        domain=domain,
        logo_phash=logo_phash,
        favicon_hash=favicon_hash,
        color_palette=[list(c) for c in color_palette] if color_palette else None,
        screenshot_url=screenshot_url,
    )


@router.get("", tags=["brands"])
async def list_brands(db: AsyncSession = Depends(get_db)):
    """List all registered brands with scan result counts per verdict."""
    from sqlalchemy import case, func
    from core.models import ScanResult

    # Aggregate verdict counts per brand in one query
    counts = await db.execute(
        select(
            ScanResult.brand_domain,
            func.count().label("total"),
            func.sum(case((ScanResult.verdict == "CRITICAL",   1), else_=0)).label("critical"),
            func.sum(case((ScanResult.verdict == "SUSPICIOUS", 1), else_=0)).label("suspicious"),
            func.sum(case((ScanResult.verdict == "CLEAN",      1), else_=0)).label("clean"),
        ).group_by(ScanResult.brand_domain)
    )
    stats = {row.brand_domain: row._asdict() for row in counts}

    brands_result = await db.execute(select(Brand).order_by(Brand.domain))
    brands = brands_result.scalars().all()

    return [
        {
            "id": b.id,
            "domain": b.domain,
            "keywords": b.keywords,
            "created_at": b.created_at,
            "total_scans":  stats.get(b.domain, {}).get("total",      0),
            "critical":     stats.get(b.domain, {}).get("critical",   0),
            "suspicious":   stats.get(b.domain, {}).get("suspicious", 0),
            "clean":        stats.get(b.domain, {}).get("clean",      0),
        }
        for b in brands
    ]


def _cancel_brand_tasks(brand_domain: str) -> dict:
    from workers.tasks import mark_brand_cancelled
    mark_brand_cancelled(brand_domain)  # blocks queued tasks immediately
    """
    Revoke all active + reserved Celery tasks that belong to this brand,
    then delete their Redis scan locks so workers stop immediately and
    new tasks for this brand can be queued cleanly.
    """
    from workers.pipeline import app as celery_app
    import redis as sync_redis

    inspector = celery_app.control.inspect(timeout=2.0)
    active    = inspector.active()   or {}
    reserved  = inspector.reserved() or {}

    revoked = 0
    lock_keys: list[str] = []

    for tasks in list(active.values()) + list(reserved.values()):
        for task in tasks:
            kwargs = task.get("kwargs", {})
            if kwargs.get("brand_domain") == brand_domain:
                celery_app.control.revoke(task["id"], terminate=True, signal="SIGKILL")
                revoked += 1
                # Collect the lock key for this (candidate, brand) pair
                candidate = kwargs.get("domain")
                if candidate:
                    lock_keys.append(f"scan:lock:{candidate}:{brand_domain}")

    # Also sweep Redis for any stale lock keys matching this brand
    r = sync_redis.from_url(settings.redis_url, decode_responses=True)
    pattern_keys = r.keys(f"scan:lock:*:{brand_domain}")
    all_keys = list(set(lock_keys + pattern_keys))
    if all_keys:
        r.delete(*all_keys)
    r.close()

    return {"revoked_tasks": revoked, "locks_cleared": len(all_keys)}


@router.delete("/{domain}", tags=["brands"])
async def delete_brand(
    domain: str,
    include_scan_results: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a brand fingerprint by domain name.
    Always stops all active/queued scan tasks for this brand.
    Pass ?include_scan_results=true to also wipe all scan results.
    """
    result = await db.execute(select(Brand).where(Brand.domain == domain))
    brand = result.scalar_one_or_none()
    if not brand:
        raise HTTPException(status_code=404, detail=f"Brand '{domain}' not found.")

    # Stop all running/queued tasks for this brand first
    cancellation = _cancel_brand_tasks(domain)

    if include_scan_results:
        await db.execute(delete(ScanResult).where(ScanResult.brand_domain == domain))

    await db.delete(brand)
    await db.commit()

    return {
        "deleted": domain,
        "scan_results_deleted": include_scan_results,
        **cancellation,
    }


@router.delete("/{domain}/results", tags=["brands"])
async def delete_brand_results(domain: str, db: AsyncSession = Depends(get_db)):
    """Delete all scan results for a brand without removing the brand itself."""
    res = await db.execute(delete(ScanResult).where(ScanResult.brand_domain == domain))
    await db.commit()
    return {"brand_domain": domain, "scan_results_deleted": res.rowcount}
