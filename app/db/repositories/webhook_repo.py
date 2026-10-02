"""
MongoDB Webhook Delivery Repository for Audit Logging and Retry Tracking.
"""

from datetime import datetime, timezone
import logging
import uuid
from typing import Any, Dict, List, Optional

from app.db.mongodb import MongoDBManager

logger = logging.getLogger("db.webhook_repo")


class WebhookRepository:
    """Stores delivery logs and status for asynchronous webhook notifications."""

    @classmethod
    def _collection(cls):
        db = MongoDBManager.get_db()
        return db["webhook_deliveries"]

    @classmethod
    async def record_delivery(
        cls,
        job_id: str,
        event: str,
        url: str,
        status_code: Optional[int],
        success: bool,
        attempts: int,
        error: Optional[str] = None,
        delivery_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Inserts a webhook delivery log record."""
        record = {
            "delivery_id": delivery_id or f"dlv_{uuid.uuid4().hex[:12]}",
            "job_id": job_id,
            "event": event,
            "url": url,
            "status_code": status_code,
            "success": success,
            "attempts": attempts,
            "error": error,
            "delivered_at": datetime.now(timezone.utc),
        }
        coll = cls._collection()
        await coll.insert_one(record)
        return record

    @classmethod
    async def get_deliveries_for_job(cls, job_id: str) -> List[Dict[str, Any]]:
        """Retrieves all webhook delivery attempts for a given job."""
        coll = cls._collection()
        cursor = coll.find({"job_id": job_id}, {"_id": 0}).sort("delivered_at", -1)
        return await cursor.to_list(length=100)
