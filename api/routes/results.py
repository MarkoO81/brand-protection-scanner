"""GET /results — query stored scan results."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from api.models import ScanResultOut
from core.models import ScanResult

router = APIRouter(tags=["results"])


@router.get("/results", response_model=list[ScanResultOut])
async def get_results(
    verdict: Optional[str] = Query(None, description="Filter by verdict: CLEAN / SUSPICIOUS / CRITICAL"),
    min_score: Optional[float] = Query(None, ge=0, le=100),
    brand_domain: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if verdict:
        filters.append(ScanResult.verdict == verdict.upper())
    if min_score is not None:
        filters.append(ScanResult.score >= min_score)
    if brand_domain:
        filters.append(ScanResult.brand_domain == brand_domain)
    if source:
        filters.append(ScanResult.source == source)

    stmt = (
        select(ScanResult)
        .where(and_(*filters))
        .order_by(ScanResult.scanned_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [ScanResultOut.model_validate(r) for r in rows]


@router.get("/results/{result_id}", response_model=ScanResultOut)
async def get_result(result_id: int, db: AsyncSession = Depends(get_db)):
    from fastapi import HTTPException

    result = await db.get(ScanResult, result_id)
    if not result:
        raise HTTPException(status_code=404, detail="Result not found.")
    return ScanResultOut.model_validate(result)
