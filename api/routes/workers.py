"""
GET  /workers/status       — live worker + queue stats + active task details
GET  /workers/throughput   — scans completed per minute / hour
POST /workers/stop         — revoke all active tasks + purge queues
DELETE /workers/tasks/{id} — revoke a single task
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from core.models import ScanResult
from core.redis_client import get_redis
from workers.pipeline import app as celery_app

router = APIRouter(prefix="/workers", tags=["workers"])

QUEUES = ["scans", "discovery", "celery"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _queue_lengths() -> dict[str, int]:
    redis = get_redis()
    lengths: dict[str, int] = {}
    for q in QUEUES:
        lengths[q] = await redis.llen(q)
    await redis.aclose()
    return lengths


def _parse_active_task(t: dict) -> dict:
    """Extract human-readable fields from a Celery active-task dict."""
    kwargs = t.get("kwargs", {})
    time_start = t.get("time_start")
    elapsed = None
    if time_start:
        elapsed = round(time.time() - time_start, 1)

    return {
        "id": t["id"],
        "name": t["name"].split(".")[-1],   # short name, e.g. "scan_domain"
        "domain": kwargs.get("domain"),
        "brand_domain": kwargs.get("brand_domain"),
        "source": kwargs.get("source", "—"),
        "elapsed_sec": elapsed,
        "worker": t.get("hostname"),
    }


def _inspect() -> dict:
    """Non-blocking Celery inspect (2 s timeout)."""
    inspector = celery_app.control.inspect(timeout=2.0)
    active   = inspector.active()   or {}
    reserved = inspector.reserved() or {}

    all_active_tasks = []
    workers = []

    for worker_name in set(list(active.keys()) + list(reserved.keys())):
        active_tasks   = active.get(worker_name, [])
        reserved_tasks = reserved.get(worker_name, [])
        parsed = [_parse_active_task(t) for t in active_tasks]
        all_active_tasks.extend(parsed)
        workers.append({
            "name": worker_name,
            "active_count": len(active_tasks),
            "reserved_count": len(reserved_tasks),
            "active_tasks": parsed,
        })

    return {
        "workers": workers,
        "active_tasks": all_active_tasks,
        "total_active": len(all_active_tasks),
        "total_reserved": sum(w["reserved_count"] for w in workers),
        "worker_count": len(workers),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/status")
async def worker_status():
    queue_lengths = await _queue_lengths()
    inspect_data  = _inspect()
    return {
        **inspect_data,
        "queues": queue_lengths,
    }


@router.get("/throughput")
async def throughput(db: AsyncSession = Depends(get_db)):
    """Scans completed in the last 1 min, 10 min, and 1 hour."""
    from datetime import timedelta

    now = datetime.now(timezone.utc)

    async def _count(minutes: int) -> int:
        since = now - timedelta(minutes=minutes)
        result = await db.execute(
            select(func.count()).where(ScanResult.scanned_at >= since)
        )
        return result.scalar_one()

    last_1m, last_10m, last_1h = (
        await _count(1),
        await _count(10),
        await _count(60),
    )

    return {
        "last_1m":  last_1m,
        "last_10m": last_10m,
        "last_1h":  last_1h,
        "rate_per_min": round(last_10m / 10, 2),
    }


@router.post("/stop")
async def stop_all():
    """Revoke every active/reserved task and purge all queues."""
    inspector = celery_app.control.inspect(timeout=2.0)
    active   = inspector.active()   or {}
    reserved = inspector.reserved() or {}

    revoked = []
    for tasks in list(active.values()) + list(reserved.values()):
        for task in tasks:
            celery_app.control.revoke(task["id"], terminate=True, signal="SIGTERM")
            revoked.append(task["id"])

    purged = celery_app.control.purge()

    return {
        "revoked_tasks": len(revoked),
        "purged_messages": purged,
        "message": "All active tasks revoked and queues purged.",
    }


@router.delete("/tasks/{task_id}")
async def stop_task(task_id: str):
    """Revoke a single task by ID."""
    celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
    return {"task_id": task_id, "message": "Task revoked."}
