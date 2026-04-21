"""
GET  /workers/status  — live worker + queue stats
POST /workers/stop    — revoke all active tasks + purge queues
DELETE /workers/tasks/{task_id} — revoke a single task
"""
from __future__ import annotations

from fastapi import APIRouter

from core.redis_client import get_redis
from workers.pipeline import app as celery_app

router = APIRouter(prefix="/workers", tags=["workers"])

QUEUES = ["scans", "discovery", "celery"]


async def _queue_lengths() -> dict[str, int]:
    redis = get_redis()
    lengths: dict[str, int] = {}
    for q in QUEUES:
        lengths[q] = await redis.llen(q)
    await redis.aclose()
    return lengths


def _inspect() -> dict:
    """Non-blocking Celery inspect with a short timeout."""
    inspector = celery_app.control.inspect(timeout=2.0)
    active   = inspector.active()   or {}
    reserved = inspector.reserved() or {}

    workers = []
    for worker_name in set(list(active.keys()) + list(reserved.keys())):
        active_tasks   = active.get(worker_name, [])
        reserved_tasks = reserved.get(worker_name, [])
        workers.append({
            "name": worker_name,
            "active_count": len(active_tasks),
            "reserved_count": len(reserved_tasks),
            "active_tasks": [
                {"id": t["id"], "name": t["name"]} for t in active_tasks
            ],
        })

    total_active   = sum(w["active_count"]   for w in workers)
    total_reserved = sum(w["reserved_count"] for w in workers)
    return {
        "workers": workers,
        "total_active": total_active,
        "total_reserved": total_reserved,
        "worker_count": len(workers),
    }


@router.get("/status")
async def worker_status():
    queue_lengths = await _queue_lengths()
    inspect_data  = _inspect()
    return {
        **inspect_data,
        "queues": queue_lengths,
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
