"""POST /sweep — generate permutations and enqueue scans for all."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from analyzers.domain import generate_permutations
from api.deps import get_db
from api.models import SweepRequest, SweepResponse
from core.models import Brand
from workers.tasks import sweep_brand

router = APIRouter(tags=["sweep"])


@router.post("/sweep", response_model=SweepResponse)
async def sweep(body: SweepRequest, db: AsyncSession = Depends(get_db)):
    brand_domain = body.domain

    # Load stored fingerprint
    result = await db.execute(select(Brand).where(Brand.domain == brand_domain))
    brand = result.scalar_one_or_none()
    brand_phash = brand.logo_phash if brand else None
    brand_palette = brand.color_palette if brand else None

    # Clear global halt so sweeps work again after a reset
    from core.redis_client import get_redis
    _r = get_redis()
    await _r.delete("global:halt")
    await _r.aclose()

    candidates = generate_permutations(brand_domain)

    task = sweep_brand.apply_async(
        kwargs={
            "brand_domain": brand_domain,
            "brand_keywords": body.keywords,
            "brand_phash": brand_phash,
            "brand_palette": brand_palette,
        }
    )

    return SweepResponse(
        brand_domain=brand_domain,
        task_id=task.id,
        candidates_estimated=len(candidates),
    )
