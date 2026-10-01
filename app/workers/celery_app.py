"""
Celery Asynchronous Task Worker Configuration.
"""

from celery import Celery
from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "ocr_advance_worker",
    broker=settings.redis.celery_broker_url,
    backend=settings.redis.celery_result_backend,
    include=[
        # Phase task modules will be included here as they are built
    ],
)

celery_app.conf.update(
    task_default_queue=settings.redis.celery_task_default_queue,
    task_time_limit=settings.redis.celery_task_time_limit,
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
)
