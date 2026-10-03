"""
Asynchronous In-Memory Event Bus and Server-Sent Events (SSE) Stream Manager.
Enables real-time push notifications of job progress, stage transitions, and completion.
"""

import asyncio
from datetime import datetime, timezone
import json
import logging
from typing import Any, AsyncGenerator, Dict, Optional, Set

logger = logging.getLogger("event_bus")


class JobEventBus:
    """Pub/Sub event broadcaster for background OCR job tracking and SSE streaming."""

    _subscribers: Dict[str, Set[asyncio.Queue]] = {}
    _global_subscribers: Set[asyncio.Queue] = set()
    _lock: Optional[asyncio.Lock] = None

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        return cls._lock

    @classmethod
    async def publish(
        cls,
        job_id: str,
        event: str,
        data: Dict[str, Any],
    ) -> None:
        """Publishes an event to all subscribers listening to this job_id."""
        if "timestamp" not in data:
            data["timestamp"] = datetime.now(timezone.utc).isoformat()
        if "event" not in data:
            data["event"] = event
        if "job_id" not in data:
            data["job_id"] = job_id

        payload_str = json.dumps(data, default=str)
        message = f"event: {event}\ndata: {payload_str}\n\n"

        async with cls._get_lock():
            # Job-specific subscribers
            if job_id in cls._subscribers:
                for q in list(cls._subscribers[job_id]):
                    try:
                        q.put_nowait(message)
                    except asyncio.QueueFull:
                        try:
                            q.get_nowait()
                            q.put_nowait(message)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.warning(f"Error pushing to job queue for {job_id}: {e}")

            # Global admin subscribers
            for q in list(cls._global_subscribers):
                try:
                    q.put_nowait(message)
                except asyncio.QueueFull:
                    try:
                        q.get_nowait()
                        q.put_nowait(message)
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f"Error pushing to global queue: {e}")

    @classmethod
    async def subscribe(
        cls,
        job_id: str,
        timeout_seconds: int = 1800,
    ) -> AsyncGenerator[str, None]:
        """
        Subscribes to live SSE events for a specific job_id.
        Yields SSE text frames until job completes/fails or connection closes.
        """
        queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        async with cls._get_lock():
            if job_id not in cls._subscribers:
                cls._subscribers[job_id] = set()
            cls._subscribers[job_id].add(queue)

        try:
            connect_msg = json.dumps({
                "event": "connected",
                "job_id": job_id,
                "message": f"Connected to live event stream for job {job_id}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            yield f"event: connected\ndata: {connect_msg}\n\n"

            start_time = asyncio.get_event_loop().time()

            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield message

                    if "event: ocr.job.completed" in message or "event: ocr.job.failed" in message or "event: ocr.job.cancelled" in message:
                        await asyncio.sleep(0.5)
                        break

                except asyncio.TimeoutError:
                    yield f": keepalive {int(asyncio.get_event_loop().time())}\n\n"

                if (asyncio.get_event_loop().time() - start_time) > timeout_seconds:
                    logger.info(f"SSE stream timed out after {timeout_seconds}s for job {job_id}")
                    break

        finally:
            async with cls._get_lock():
                if job_id in cls._subscribers:
                    cls._subscribers[job_id].discard(queue)
                    if not cls._subscribers[job_id]:
                        del cls._subscribers[job_id]
            logger.info(f"Client disconnected from SSE stream for job {job_id}")

    @classmethod
    async def subscribe_global(cls) -> AsyncGenerator[str, None]:
        """Subscribes to all global system events (e.g. for live Admin Dashboard)."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        async with cls._get_lock():
            cls._global_subscribers.add(queue)

        try:
            yield "event: connected\ndata: {\"message\": \"Connected to global admin event stream\"}\n\n"
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield message
                except asyncio.TimeoutError:
                    yield f": keepalive\n\n"
        finally:
            async with cls._get_lock():
                cls._global_subscribers.discard(queue)

    @classmethod
    def reset(cls) -> None:
        """Resets event bus state across tests."""
        cls._subscribers.clear()
        cls._global_subscribers.clear()
        cls._lock = None
