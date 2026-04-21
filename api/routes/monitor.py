"""POST /monitor/certstream — start real-time Certstream monitoring."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks

from api.models import MonitorRequest, MonitorResponse
from monitor.certstream import run_certstream_monitor

router = APIRouter(prefix="/monitor", tags=["monitor"])

# Track active monitors (domain → task) to prevent duplicates
_active_monitors: dict[str, asyncio.Task] = {}


@router.post("/certstream", response_model=MonitorResponse)
async def start_certstream(body: MonitorRequest, background_tasks: BackgroundTasks):
    domain = body.domain

    if domain in _active_monitors and not _active_monitors[domain].done():
        return MonitorResponse(
            message=f"Certstream monitor already active for {domain}.",
            domain=domain,
        )

    async def _run():
        await run_certstream_monitor(domain, body.keywords)

    task = asyncio.create_task(_run())
    _active_monitors[domain] = task

    return MonitorResponse(
        message=f"Certstream monitor started for {domain}.",
        domain=domain,
    )


@router.delete("/certstream/{domain}", tags=["monitor"])
async def stop_certstream(domain: str):
    task = _active_monitors.pop(domain, None)
    if task and not task.done():
        task.cancel()
        return {"message": f"Monitor stopped for {domain}."}
    return {"message": f"No active monitor found for {domain}."}


@router.get("/certstream", tags=["monitor"])
async def list_monitors():
    return {
        "active": [d for d, t in _active_monitors.items() if not t.done()]
    }
