"""
POST /admin/reset — stop everything and wipe the database.
"""
from __future__ import annotations

import asyncio
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from core.config import settings
from core.redis_client import get_redis
from workers.pipeline import app as celery_app

router = APIRouter(prefix="/admin", tags=["admin"])

CELERY_QUEUE_KEYS = [
    "scans", "discovery", "celery",
    "_kombu.binding.scans", "_kombu.binding.discovery", "_kombu.binding.celery",
    "unacked", "unacked_index",
]


def _stop_celery_tasks() -> dict:
    """Synchronous — run in executor so it doesn't block the event loop."""
    revoked = 0
    try:
        inspector = celery_app.control.inspect(timeout=2.0)
        active    = inspector.active()   or {}
        reserved  = inspector.reserved() or {}
        for tasks in list(active.values()) + list(reserved.values()):
            for task in tasks:
                try:
                    celery_app.control.revoke(task["id"], terminate=True, signal="SIGKILL")
                    revoked += 1
                except Exception:
                    pass
    except Exception:
        pass  # Workers may be unreachable — that's fine, queues get cleared below
    return {"revoked": revoked}


@router.post("/reset")
async def full_reset(db: AsyncSession = Depends(get_db)):
    """Hard reset — stop all scans, purge queues, wipe database."""
    try:
        # 1. Revoke active tasks (blocking call → thread executor)
        loop = asyncio.get_event_loop()
        celery_result = await loop.run_in_executor(None, _stop_celery_tasks)
        revoked = celery_result["revoked"]

        # 2. Directly flush Redis queue lists + set halt flag + clear locks
        redis = get_redis()

        queue_cleared = 0
        for key in CELERY_QUEUE_KEYS:
            try:
                count = await redis.llen(key)
                if count:
                    await redis.delete(key)
                    queue_cleared += count
            except Exception:
                pass

        # Global halt flag — tasks check this and exit immediately
        await redis.set("global:halt", "1", ex=3600)

        # Clear all lock and cancellation keys
        meta_keys: list[str] = []
        for pattern in ["scan:lock:*", "brand:cancelled:*"]:
            try:
                keys = await redis.keys(pattern)
                meta_keys.extend(keys)
            except Exception:
                pass
        if meta_keys:
            await redis.delete(*meta_keys)

        await redis.aclose()

        # 3. Truncate DB tables
        await db.execute(text("TRUNCATE TABLE scan_results RESTART IDENTITY CASCADE"))
        await db.execute(text("TRUNCATE TABLE brands RESTART IDENTITY CASCADE"))
        await db.commit()

        return {
            "revoked_tasks":      revoked,
            "queue_msgs_cleared": queue_cleared,
            "meta_keys_cleared":  len(meta_keys),
            "message": "Hard reset complete.",
        }

    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
