"""POST /brands/fingerprint — capture brand visual fingerprint."""
from __future__ import annotations

import os
from typing import Optional

import tldextract
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

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


@router.post("/fingerprint", response_model=FingerprintResponse)
async def fingerprint_brand(
    body: FingerprintRequest,
    db: AsyncSession = Depends(get_db),
):
    url = body.brand_url
    extracted = tldextract.extract(url)
    domain = f"{extracted.domain}.{extracted.suffix}"

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
    """List all registered brand domains."""
    result = await db.execute(select(Brand).order_by(Brand.domain))
    brands = result.scalars().all()
    return [
        {"id": b.id, "domain": b.domain, "keywords": b.keywords, "created_at": b.created_at}
        for b in brands
    ]


@router.delete("/{domain}", tags=["brands"])
async def delete_brand(
    domain: str,
    include_scan_results: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """
    Delete a brand fingerprint by domain name.
    Pass ?include_scan_results=true to also wipe all scan results for this brand.
    """
    result = await db.execute(select(Brand).where(Brand.domain == domain))
    brand = result.scalar_one_or_none()
    if not brand:
        raise HTTPException(status_code=404, detail=f"Brand '{domain}' not found.")

    if include_scan_results:
        await db.execute(delete(ScanResult).where(ScanResult.brand_domain == domain))

    await db.delete(brand)
    await db.commit()

    return {
        "deleted": domain,
        "scan_results_deleted": include_scan_results,
    }


@router.delete("/{domain}/results", tags=["brands"])
async def delete_brand_results(domain: str, db: AsyncSession = Depends(get_db)):
    """Delete all scan results for a brand without removing the brand itself."""
    res = await db.execute(delete(ScanResult).where(ScanResult.brand_domain == domain))
    await db.commit()
    return {"brand_domain": domain, "scan_results_deleted": res.rowcount}
