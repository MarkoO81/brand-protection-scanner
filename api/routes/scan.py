"""POST /scan — enqueue a single-domain scan."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from api.models import ScanRequest, ScanResponse
from core.models import Brand
from workers.tasks import scan_domain

router = APIRouter(tags=["scan"])


@router.post("/scan", response_model=ScanResponse)
async def scan(body: ScanRequest, db: AsyncSession = Depends(get_db)):
    brand_domain = body.brand_config.domain
    keywords = body.brand_config.keywords

    # Load stored brand fingerprint if available
    result = await db.execute(select(Brand).where(Brand.domain == brand_domain))
    brand = result.scalar_one_or_none()
    brand_phash = brand.logo_phash if brand else None
    brand_palette = brand.color_palette if brand else None

    task = scan_domain.apply_async(
        kwargs={
            "domain": body.domain,
            "brand_domain": brand_domain,
            "brand_keywords": keywords,
            "brand_phash": brand_phash,
            "brand_palette": brand_palette,
            "source": "manual",
        }
    )

    return ScanResponse(task_id=task.id, domain=body.domain, brand_domain=brand_domain)
