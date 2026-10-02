"""
Asynchronous Worker Manager and Task Processing Engine.
Executes OCR jobs independently in the background, coordinates state in MongoDB,
broadcasts Server-Sent Events, and dispatches signed webhooks.
"""

import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Any, Dict, Optional
import psutil

from app.core.config import get_settings
from app.db.repositories.document_repo import DocumentRepository
from app.db.repositories.job_repo import JobRepository
from app.schemas.job import JobStatusEnum
from app.schemas.webhook import WebhookEventEnum
from app.services.event_bus import JobEventBus
from app.services.pipeline import ExtractionPipeline
from app.services.storage_service import StorageService
from app.services.webhook_service import WebhookService

logger = logging.getLogger("workers.manager")


class WorkerManager:
    """Manages asynchronous document processing jobs, concurrency limits, and crash recovery."""

    _queue: Optional[asyncio.Queue] = None
    _workers: list[asyncio.Task] = []
    _active_jobs_count: int = 0
    _start_time: float = time.time()
    _initialized: bool = False
    _lock: Optional[asyncio.Lock] = None

    @classmethod
    def _get_queue(cls) -> asyncio.Queue:
        if cls._queue is None:
            cls._queue = asyncio.Queue()
        return cls._queue

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    async def start(cls) -> None:
        """Starts the background worker pool and runs crash recovery for stale jobs."""
        if cls._initialized and cls._workers:
            return

        settings = get_settings()
        cls._queue = asyncio.Queue()
        cls._lock = asyncio.Lock()
        cls._active_jobs_count = 0
        cls._start_time = time.time()

        logger.info(f"🚀 Starting WorkerManager with concurrency limit = {settings.worker_concurrency}...")

        # 1. Recover any stale jobs from previous worker crashes
        try:
            await cls.recover_stale_jobs()
        except Exception as e:
            logger.warning(f"Warning during stale job recovery on startup: {e}")

        # 2. Spawn worker tasks
        cls._workers = [
            asyncio.create_task(cls._worker_loop(worker_idx=i + 1))
            for i in range(settings.worker_concurrency)
        ]
        cls._initialized = True
        logger.info(f"✅ {len(cls._workers)} background workers active and listening for OCR jobs.")

    @classmethod
    async def stop(cls) -> None:
        """Gracefully shuts down background worker pool."""
        logger.info("🛑 Shutting down WorkerManager worker tasks...")
        for w in cls._workers:
            w.cancel()
        if cls._workers:
            await asyncio.gather(*cls._workers, return_exceptions=True)
        cls._workers.clear()
        cls._queue = None
        cls._lock = None
        cls._initialized = False
        logger.info("WorkerManager stopped.")

    @classmethod
    async def submit_job(cls, job_id: str) -> None:
        """Submits a job to the background queue."""
        settings = get_settings()
        if settings.use_celery:
            try:
                from app.workers.tasks import process_ocr_document_task
                process_ocr_document_task.delay(job_id)
                logger.info(f"Dispatched job {job_id} to Celery queue.")
                return
            except Exception as e:
                logger.warning(f"Could not dispatch to Celery ({e}), falling back to internal worker queue.")

        # Ensure manager is started
        if not cls._initialized or not cls._workers:
            await cls.start()

        queue = cls._get_queue()
        await queue.put(job_id)
        logger.info(f"Enqueued job {job_id} to internal async worker queue.")

    @classmethod
    async def _worker_loop(cls, worker_idx: int) -> None:
        """Continuous worker loop processing jobs from the queue."""
        logger.info(f"Worker #{worker_idx} ready.")
        queue = cls._get_queue()
        while True:
            try:
                job_id = await queue.get()
                lock = cls._get_lock()
                async with lock:
                    cls._active_jobs_count += 1

                try:
                    logger.info(f"[Worker #{worker_idx}] Picking up job: {job_id}")
                    await cls.process_job(job_id)
                except Exception as e:
                    logger.error(f"[Worker #{worker_idx}] Unhandled error processing job {job_id}: {e}", exc_info=True)
                finally:
                    async with lock:
                        cls._active_jobs_count = max(0, cls._active_jobs_count - 1)
                    queue.task_done()

            except asyncio.CancelledError:
                logger.info(f"Worker #{worker_idx} received cancellation signal.")
                break
            except Exception as e:
                logger.error(f"Worker #{worker_idx} encountered unexpected loop error: {e}")
                await asyncio.sleep(1)

    @classmethod
    async def process_job(cls, job_id: str) -> None:
        """
        Executes a background OCR processing job from end to end.
        """
        job = await JobRepository.get_job(job_id)
        if not job:
            logger.error(f"Cannot process job {job_id}: Job not found in MongoDB.")
            return

        # Idempotency check: don't process completed or cancelled jobs
        if job.get("status") in ("completed", "cancelled"):
            logger.info(f"Job {job_id} is already {job.get('status')}. Skipping.")
            return

        doc_id = job.get("document_id") or f"doc_{job_id}"
        callback_url = job.get("callback_url")
        callback_secret = job.get("callback_secret")
        file_ref = job.get("file_reference")
        owner_hash = job.get("user_id")

        try:
            # 1. Mark Job as Started
            await JobRepository.start_job(job_id, total_pages=job.get("total_pages", 0))

            # Broadcast Started Event
            start_event_data = {
                "job_id": job_id,
                "document_id": doc_id,
                "status": "processing",
                "current_stage": "ocr_extraction",
                "progress": 5,
                "total_pages": job.get("total_pages", 0),
                "processed_pages": 0,
                "message": "Processing started by background worker",
            }
            await JobEventBus.publish(job_id, WebhookEventEnum.JOB_STARTED.value, start_event_data)

            if callback_url:
                asyncio.create_task(
                    WebhookService.dispatch_event(
                        event_name=WebhookEventEnum.JOB_STARTED.value,
                        job_id=job_id,
                        document_id=doc_id,
                        callback_url=callback_url,
                        callback_secret=callback_secret,
                        status="processing",
                        progress=5,
                        current_stage="ocr_extraction",
                        metadata={"total_pages": job.get("total_pages", 0)},
                    )
                )

            # 2. Read File Bytes from Storage
            if not file_ref:
                raise ValueError("No file_reference found on job record.")
            file_bytes = await StorageService.read_file(file_ref)
            filename = job.get("metadata", {}).get("filename", "document.pdf")

            # 3. Create Progress Callback for Live Updates
            async def _progress_callback(
                processed_pages: int,
                total_pages: int,
                stage: str,
                progress_pct: int,
                message: str,
            ) -> None:
                await JobRepository.update_progress(
                    job_id=job_id,
                    stage=stage,
                    progress=progress_pct,
                    processed_pages=processed_pages,
                    total_pages=total_pages,
                    message=message,
                )
                event_data = {
                    "job_id": job_id,
                    "document_id": doc_id,
                    "status": "processing",
                    "progress": progress_pct,
                    "total_pages": total_pages,
                    "processed_pages": processed_pages,
                    "current_stage": stage,
                    "message": message,
                }
                await JobEventBus.publish(job_id, WebhookEventEnum.JOB_PROGRESS.value, event_data)

            # 4. Execute Pipeline
            pipeline = ExtractionPipeline()
            result = await pipeline.process_document(
                file=None,
                file_bytes=file_bytes,
                filename=filename,
                document_type=job.get("document_type", "auto"),
                language=job.get("language", "en"),
                clean_with_ai=job.get("clean_with_ai", True),
                client_request_id=doc_id,
                password=job.get("password"),
                owner_hash=owner_hash,
                job_id=job_id,
                progress_callback=_progress_callback,
            )

            # 5. Mark Job as Completed
            meta_dict = result.metadata.model_dump()
            await JobRepository.complete_job(
                job_id=job_id,
                result_dict=result.model_dump(),
                document_id=doc_id,
                metadata_dict=meta_dict,
            )

            result_url = f"/api/v1/documents/{doc_id}"

            # Broadcast SSE Completed Event
            completed_event_data = {
                "job_id": job_id,
                "document_id": doc_id,
                "status": "completed",
                "progress": 100,
                "total_pages": result.metadata.pages,
                "processed_pages": result.metadata.pages,
                "current_stage": "completed",
                "message": "Processing completed successfully",
                "result_url": result_url,
                "metadata": meta_dict,
            }
            await JobEventBus.publish(job_id, WebhookEventEnum.JOB_COMPLETED.value, completed_event_data)

            # 6. Dispatch Webhook
            if callback_url:
                await WebhookService.dispatch_event(
                    event_name=WebhookEventEnum.JOB_COMPLETED.value,
                    job_id=job_id,
                    document_id=doc_id,
                    callback_url=callback_url,
                    callback_secret=callback_secret,
                    status="completed",
                    progress=100,
                    current_stage="completed",
                    metadata={
                        "total_pages": result.metadata.pages,
                        "processed_pages": result.metadata.pages,
                        "processing_time_ms": result.metadata.processing_time_ms,
                    },
                    result_url=result_url,
                )

            logger.info(f"✅ Background job {job_id} completed successfully.")

        except Exception as e:
            error_msg = str(e)
            logger.error(f"❌ Background job {job_id} failed: {error_msg}")

            settings = get_settings()
            current_retries = job.get("retry_count", 0)

            # Determine if retryable
            if current_retries < settings.max_job_retries and not isinstance(e, ValueError):
                new_count = await JobRepository.increment_retry(job_id)
                logger.info(f"Retrying job {job_id} (Attempt #{new_count})...")
                await cls.submit_job(job_id)
                return

            # Retries exhausted or non-retryable error
            await JobRepository.fail_job(job_id, error_message=error_msg)

            failed_event_data = {
                "job_id": job_id,
                "document_id": doc_id,
                "status": "failed",
                "progress": 0,
                "current_stage": "failed",
                "message": f"Processing failed: {error_msg}",
                "error": error_msg,
            }
            await JobEventBus.publish(job_id, WebhookEventEnum.JOB_FAILED.value, failed_event_data)

            if callback_url:
                await WebhookService.dispatch_event(
                    event_name=WebhookEventEnum.JOB_FAILED.value,
                    job_id=job_id,
                    document_id=doc_id,
                    callback_url=callback_url,
                    callback_secret=callback_secret,
                    status="failed",
                    progress=0,
                    current_stage="failed",
                    error=error_msg,
                )

    @classmethod
    async def recover_stale_jobs(cls) -> int:
        """
        Crash Recovery: Scans MongoDB for jobs stuck in 'processing' state
        from an unexpected server/worker restart and re-enqueues them or marks them failed.
        """
        settings = get_settings()
        stale_jobs = await JobRepository.get_stale_processing_jobs(max_age_seconds=settings.job_stale_timeout_seconds)
        recovered_count = 0
        queue = cls._get_queue()

        for job in stale_jobs:
            job_id = job.get("job_id")
            retries = job.get("retry_count", 0)
            if retries < settings.max_job_retries:
                logger.warning(f"Recovering orphaned job {job_id} after worker restart (re-queuing)...")
                await JobRepository.increment_retry(job_id)
                await queue.put(job_id)
                recovered_count += 1
            else:
                logger.warning(f"Marking orphaned job {job_id} as failed (max retries reached).")
                await JobRepository.fail_job(job_id, "Worker terminated unexpectedly during processing.")

        if recovered_count > 0:
            logger.info(f"✅ Recovered {recovered_count} orphaned background jobs.")
        return recovered_count

    @classmethod
    def get_health_stats(cls) -> Dict[str, Any]:
        """Returns runtime performance metrics for admin dashboard."""
        settings = get_settings()
        uptime = round(time.time() - cls._start_time, 1)

        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory().percent
        except Exception:
            cpu, mem = None, None

        q_size = cls._queue.qsize() if cls._queue is not None else 0

        return {
            "mode": "celery" if settings.use_celery else "async_worker_pool",
            "active_workers": len(cls._workers),
            "concurrency_limit": settings.worker_concurrency,
            "current_active_jobs": cls._active_jobs_count,
            "queue_size": q_size,
            "celery_connected": settings.use_celery,
            "redis_connected": bool(settings.redis_url),
            "mongodb_connected": True,
            "uptime_seconds": uptime,
            "cpu_percent": cpu,
            "memory_percent": mem,
        }
