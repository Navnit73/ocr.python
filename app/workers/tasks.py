"""
Celery Task Definitions for Distributed Document OCR and AI Processing.
"""

import asyncio
import logging
from app.workers.celery_app import celery_app
from app.workers.worker_manager import WorkerManager
from app.db.mongodb import MongoDBManager

logger = logging.getLogger("celery.tasks")


@celery_app.task(
    name="process_ocr_document_task",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def process_ocr_document_task(self, job_id: str, user_email: Optional[str] = None):
    """
    Celery task that executes background OCR processing.
    Runs the asynchronous WorkerManager.process_job in an event loop.
    """
    logger.info(f"[Celery Worker] Received task for job: {job_id} (user: {user_email})")

    async def _run():
        await MongoDBManager.connect()
        await WorkerManager.process_job(job_id, user_email=user_email)

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If already running inside an active event loop
            future = asyncio.run_coroutine_threadsafe(_run(), loop)
            return future.result()
        else:
            return asyncio.run(_run())
    except Exception as exc:
        logger.error(f"[Celery Worker] Task failed for job {job_id}: {exc}")
        # Celery retry for transient failures
        raise self.retry(exc=exc)
