"""
MongoDB Reconciliation Repository for Bank-vs-Ledger Reconciliation Records.
"""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional

from app.db.mongodb import MongoDBManager

logger = logging.getLogger("db.reconciliation_repo")

class ReconciliationRepository:
    """Persists one reconciliation record per submission and its final report."""

    @classmethod
    def _collection(cls):
        db = MongoDBManager.get_db()
        return db["reconciliations"]

    @classmethod
    async def create(cls, record: Dict[str, Any]) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        record.setdefault("created_at", now)
        record.setdefault("updated_at", now)
        await cls._collection().insert_one(dict(record))
        logger.info(f"Created reconciliation record: {record['reconciliation_id']}")
        return record

    @classmethod
    async def get(
        cls,
        reconciliation_id: str,
        projection: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        return await cls._collection().find_one(
            {"reconciliation_id": reconciliation_id},
            projection or {"_id": 0},
        )

    @classmethod
    async def claim_for_reconciliation(cls, reconciliation_id: str, stale_after_seconds: int = 600) -> bool:
        """
        Atomically moves a record into the 'reconciling' stage so concurrent workers or retries
        never reconcile (or notify) twice. A claim older than stale_after_seconds can be re-taken.
        """
        now = datetime.now(timezone.utc)
        stale_before = now - timedelta(seconds=stale_after_seconds)
        res = await cls._collection().update_one(
            {
                "reconciliation_id": reconciliation_id,
                "status": {"$in": ["queued", "processing"]},
                "$or": [
                    {"stage": {"$ne": "reconciling"}},
                    {"claimed_at": {"$lt": stale_before}},
                ],
            },
            {
                "$set": {
                    "status": "processing",
                    "stage": "reconciling",
                    "progress": 90,
                    "message": "Matching transactions and verifying balances",
                    "claimed_at": now,
                    "updated_at": now,
                }
            },
        )
        return res.modified_count > 0

    @classmethod
    async def complete(cls, reconciliation_id: str, result: Dict[str, Any]) -> bool:
        now = datetime.now(timezone.utc)
        res = await cls._collection().update_one(
            {"reconciliation_id": reconciliation_id, "status": {"$ne": "completed"}},
            {
                "$set": {
                    "status": "completed",
                    "stage": "completed",
                    "progress": 100,
                    "message": "Reconciliation completed successfully.",
                    "result": result,
                    "error": None,
                    "completed_at": now,
                    "updated_at": now,
                }
            },
        )
        return res.modified_count > 0

    @classmethod
    async def fail(cls, reconciliation_id: str, error: str) -> bool:
        now = datetime.now(timezone.utc)
        res = await cls._collection().update_one(
            {"reconciliation_id": reconciliation_id, "status": {"$nin": ["completed", "failed"]}},
            {
                "$set": {
                    "status": "failed",
                    "stage": "failed",
                    "message": "Reconciliation failed.",
                    "result": None,
                    "error": error,
                    "completed_at": now,
                    "updated_at": now,
                }
            },
        )
        return res.modified_count > 0

    @classmethod
    async def mark_processing(cls, reconciliation_id: str) -> None:
        await cls._collection().update_one(
            {"reconciliation_id": reconciliation_id, "status": "queued"},
            {"$set": {"status": "processing", "stage": "extracting", "updated_at": datetime.now(timezone.utc)}},
        )

    @classmethod
    async def record_webhook_delivery(
        cls,
        reconciliation_id: str,
        event: str,
        delivered: bool,
    ) -> None:
        """Stores delivery status and drops the signing secret once delivery has finished."""
        now = datetime.now(timezone.utc)
        await cls._collection().update_one(
            {"reconciliation_id": reconciliation_id},
            {
                "$set": {
                    "webhook.delivery_status": "delivered" if delivered else "failed",
                    "webhook.event": event,
                    "webhook.last_attempt_at": now,
                    "updated_at": now,
                },
                "$unset": {"webhook.secret": ""},
            },
        )

    @classmethod
    async def get_result_section(
        cls,
        reconciliation_id: str,
        section: str,
        skip: int,
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Reads one page of a saved result array without loading the full report."""
        pipeline = [
            {"$match": {"reconciliation_id": reconciliation_id}},
            {"$project": {"_id": 0, "items": {"$slice": [{"$ifNull": [f"$result.{section}", []]}, skip, limit]}}},
        ]
        docs = await cls._collection().aggregate(pipeline).to_list(length=1)
        return docs[0].get("items") or [] if docs else []
