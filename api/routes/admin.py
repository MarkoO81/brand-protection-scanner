"""
POST /admin/reset — stop everything and wipe the database.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from core.redis_client import get_redis
from workers.pipeline import app as celery_app

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/reset")
async def full_reset(db: AsyncSession = Depends(get_db)):
    """
    1. Revoke every active + reserved Celery task
    2. Purge all Celery queues
    3. Delete all scan:lock:* and brand:cancelled:* keys from Redis
    4. Truncate brands and scan_results tables
    """

    # --- 1. Revoke active + reserved tasks ---
    inspector = celery_app.control.inspect(timeout=2.0)
    active    = inspector.active()   or {}
    reserved  = inspector.reserved() or {}

    revoked = 0
    for tasks in list(active.values()) + list(reserved.values()):
        for task in tasks:
            celery_app.control.revoke(task["id"], terminate=True, signal="SIGTERM")
            revoked += 1

    # --- 2. Purge queues ---
    purged = celery_app.control.purge()

    # --- 3. Clear Redis keys (locks + cancellation flags) ---
    redis = get_redis()
    lock_keys   = await redis.keys("scan:lock:*")
    cancel_keys = await redis.keys("brand:cancelled:*")
    all_keys    = lock_keys + cancel_keys
    if all_keys:
        await redis.delete(*all_keys)
    await redis.aclose()

    # --- 4. Truncate DB tables ---
    await db.execute(text("TRUNCATE TABLE scan_results RESTART IDENTITY CASCADE"))
    await db.execute(text("TRUNCATE TABLE brands RESTART IDENTITY CASCADE"))
    await db.commit()

    return {
        "revoked_tasks":   revoked,
        "purged_messages": purged,
        "redis_keys_cleared": len(all_keys),
        "message": "All tasks stopped, queues purged, database wiped.",
    }
