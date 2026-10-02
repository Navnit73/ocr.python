"""
Celery Application Configuration for Distributed Background OCR Workers.
"""

import os
from celery import Celery
from app.core.config import get_settings

settings = get_settings()

broker_url = settings.celery_broker_url or settings.redis_url
result_backend = settings.celery_result_backend or settings.redis_url

celery_app = Celery(
    "ocr_advance_workers",
    broker=broker_url,
    backend=result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=settings.ocr_timeout * 3,  # Max runtime per task
    task_soft_time_limit=settings.ocr_timeout * 2,
    worker_prefetch_multiplier=1,  # Bounded consumption for heavy OCR workloads
    worker_max_tasks_per_child=50,  # Prevent memory leaks from PyMuPDF/OpenCV
    task_acks_late=True,  # Re-queue on worker crash
    task_reject_on_worker_lost=True,
)
