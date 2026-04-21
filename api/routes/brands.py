"""POST /brands/fingerprint — capture brand visual fingerprint."""
from __future__ import annotations

import os
from typing import Optional

import tldextract
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
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
from core.models import Brand

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
