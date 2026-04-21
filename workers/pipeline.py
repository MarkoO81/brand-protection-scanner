"""
Celery application + periodic beat schedule.
"""
from celery import Celery
from celery.schedules import crontab

from core.config import settings

app = Celery(
    "brand_protection",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["workers.tasks"],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "workers.tasks.scan_domain": {"queue": "scans"},
        "workers.tasks.sweep_brand": {"queue": "discovery"},
        "workers.tasks.periodic_sweep": {"queue": "discovery"},
    },
    beat_schedule={
        "periodic-sweep-every-6h": {
            "task": "workers.tasks.periodic_sweep",
            "schedule": crontab(minute=0, hour="*/6"),
        },
    },
)
