"""
POST /admin/reset — stop everything and wipe the database.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from core.config import settings
from core.redis_client import get_redis
from workers.pipeline import app as celery_app

router = APIRouter(prefix="/admin", tags=["admin"])

# All Redis keys that Celery uses for its queues + internal state
CELERY_QUEUE_KEYS = ["scans", "discovery", "celery", "_kombu.binding.scans",
                     "_kombu.binding.discovery", "_kombu.binding.celery",
                     "unacked", "unacked_index"]


@router.post("/reset")
async def full_reset(db: AsyncSession = Depends(get_db)):
    """
    Hard reset — stops every scan and wipes all data:
    1. SIGTERM all active + reserved Celery tasks
    2. Directly DELETE the Redis queue lists (guaranteed empty)
    3. Mark a global halt flag so any task that sneaks through exits immediately
    4. Clear all scan locks and brand cancellation keys
    5. Truncate brands and scan_results tables
    """
    redis = get_redis()

    # --- 1. Revoke active + reserved tasks via Celery control ---
    inspector = celery_app.control.inspect(timeout=2.0)
    active    = inspector.active()   or {}
    reserved  = inspector.reserved() or {}

    revoked = 0
    for tasks in list(active.values()) + list(reserved.values()):
        for task in tasks:
            celery_app.control.revoke(task["id"], terminate=True, signal="SIGTERM")
            revoked += 1

    # --- 2. Directly flush the Redis queue lists (the reliable way) ---
    queue_cleared = 0
    for key in CELERY_QUEUE_KEYS:
        count = await redis.llen(key)
        if count:
            await redis.delete(key)
            queue_cleared += count

    # --- 3. Global halt flag — tasks check this and exit in <1 ms ---
    await redis.set("global:halt", "1", ex=3600)

    # --- 4. Clear all lock and cancellation keys ---
    patterns = ["scan:lock:*", "brand:cancelled:*"]
    meta_keys: list[str] = []
    for pattern in patterns:
        meta_keys.extend(await redis.keys(pattern))
    if meta_keys:
        await redis.delete(*meta_keys)

    await redis.aclose()

    # --- 5. Truncate DB ---
    await db.execute(text("TRUNCATE TABLE scan_results RESTART IDENTITY CASCADE"))
    await db.execute(text("TRUNCATE TABLE brands RESTART IDENTITY CASCADE"))
    await db.commit()

    return {
        "revoked_tasks":      revoked,
        "queue_msgs_cleared": queue_cleared,
        "meta_keys_cleared":  len(meta_keys),
        "message": "Hard reset complete. All tasks stopped, queues emptied, database wiped.",
    }
