"""
MongoDB Job Repository for Persistent Task Status and Progress Management.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.db.mongodb import MongoDBManager

logger = logging.getLogger("db.job_repo")


class JobRepository:
    """Handles CRUD operations and atomic status updates for background OCR jobs."""

    @classmethod
    def _collection(cls):
        db = MongoDBManager.get_db()
        return db["jobs"]

    @classmethod
    async def create_job(cls, job_data: Dict[str, Any]) -> Dict[str, Any]:
        """Inserts a new job record in MongoDB."""
        now = datetime.now(timezone.utc)
        user_email = (job_data.get("user_email") or job_data.get("metadata", {}).get("user_email") or "guest").lower().strip()
        metadata = dict(job_data.get("metadata", {}))
        metadata["user_email"] = user_email
        doc = {
            "job_id": job_data["job_id"],
            "document_id": job_data.get("document_id"),
            "user_id": job_data.get("user_id"),
            "user_email": user_email,
            "status": job_data.get("status", "queued"),
            "progress": job_data.get("progress", 0),
            "total_pages": job_data.get("total_pages", 0),
            "processed_pages": job_data.get("processed_pages", 0),
            "current_stage": job_data.get("current_stage", "queued"),
            "message": job_data.get("message", "Job queued for processing"),
            "file_reference": job_data.get("file_reference"),
            "result_reference": job_data.get("result_reference"),
            "error": job_data.get("error"),
            "retry_count": job_data.get("retry_count", 0),
            "created_at": job_data.get("created_at", now),
            "updated_at": job_data.get("updated_at", now),
            "started_at": job_data.get("started_at"),
            "completed_at": job_data.get("completed_at"),
            "callback_url": job_data.get("callback_url"),
            "callback_secret": job_data.get("callback_secret"),
            "document_type": job_data.get("document_type", "auto"),
            "language": job_data.get("language", "en"),
            "clean_with_ai": job_data.get("clean_with_ai", True),
            "password": job_data.get("password"),
            "metadata": metadata,
            "result": job_data.get("result"),
        }
        coll = cls._collection()
        await coll.insert_one(doc)
        logger.info(f"Created job record in DB: {job_data['job_id']}")
        return doc

    @classmethod
    async def get_job(
        cls,
        job_id: str,
        owner_hash: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieves a job by ID, optionally verifying caller ownership to prevent IDOR."""
        coll = cls._collection()
        query: Dict[str, Any] = {"job_id": job_id}
        doc = await coll.find_one(query, {"_id": 0})
        if not doc:
            return None
        if owner_hash and doc.get("user_id") and doc.get("user_id") != owner_hash:
            # Ownership mismatch
            return None
        return doc

    @classmethod
    async def update_job(cls, job_id: str, updates: Dict[str, Any]) -> bool:
        """Updates arbitrary fields on a job record."""
        coll = cls._collection()
        updates["updated_at"] = datetime.now(timezone.utc)
        res = await coll.update_one({"job_id": job_id}, {"$set": updates})
        return res.modified_count > 0 or res.matched_count > 0

    @classmethod
    async def start_job(cls, job_id: str, total_pages: int = 0) -> bool:
        """Marks a job as started and processing."""
        now = datetime.now(timezone.utc)
        coll = cls._collection()
        res = await coll.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "status": "processing",
                    "current_stage": "ocr_extraction",
                    "started_at": now,
                    "updated_at": now,
                    "total_pages": total_pages,
                    "message": "Processing document OCR and structure extraction",
                }
            },
        )
        return res.modified_count > 0 or res.matched_count > 0

    @classmethod
    async def update_progress(
        cls,
        job_id: str,
        stage: str,
        progress: int,
        processed_pages: int,
        total_pages: int,
        message: str = "",
    ) -> bool:
        """Updates live progress metrics for a job."""
        now = datetime.now(timezone.utc)
        coll = cls._collection()
        res = await coll.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "current_stage": stage,
                    "progress": progress,
                    "processed_pages": processed_pages,
                    "total_pages": total_pages,
                    "message": message,
                    "updated_at": now,
                }
            },
        )
        return res.modified_count > 0 or res.matched_count > 0

    @classmethod
    async def complete_job(
        cls,
        job_id: str,
        result_dict: Dict[str, Any],
        document_id: Optional[str] = None,
        metadata_dict: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Marks a job as completed and stores the extraction result."""
        now = datetime.now(timezone.utc)
        coll = cls._collection()
        update_data: Dict[str, Any] = {
            "status": "completed",
            "progress": 100,
            "current_stage": "completed",
            "message": "Processing completed successfully",
            "completed_at": now,
            "updated_at": now,
            "result": result_dict,
        }
        if document_id:
            update_data["document_id"] = document_id
            update_data["result_reference"] = f"/api/v1/documents/{document_id}"
        if metadata_dict:
            update_data["metadata"] = metadata_dict

        res = await coll.update_one({"job_id": job_id}, {"$set": update_data})
        logger.info(f"Job {job_id} marked as completed in DB.")
        return res.modified_count > 0 or res.matched_count > 0

    @classmethod
    async def fail_job(cls, job_id: str, error_message: str) -> bool:
        """Marks a job as failed with error details."""
        now = datetime.now(timezone.utc)
        coll = cls._collection()
        res = await coll.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "status": "failed",
                    "current_stage": "failed",
                    "error": error_message,
                    "message": f"Processing failed: {error_message}",
                    "updated_at": now,
                }
            },
        )
        logger.warning(f"Job {job_id} marked as failed in DB: {error_message}")
        return res.modified_count > 0 or res.matched_count > 0

    @classmethod
    async def cancel_job(cls, job_id: str, owner_hash: Optional[str] = None) -> bool:
        """Cancels a queued or processing job."""
        now = datetime.now(timezone.utc)
        coll = cls._collection()
        query: Dict[str, Any] = {
            "job_id": job_id,
            "status": {"$in": ["queued", "processing"]},
        }
        if owner_hash:
            query["user_id"] = owner_hash

        res = await coll.update_one(
            query,
            {
                "$set": {
                    "status": "cancelled",
                    "current_stage": "cancelled",
                    "message": "Job was cancelled by user request",
                    "updated_at": now,
                }
            },
        )
        return res.modified_count > 0

    @classmethod
    async def increment_retry(cls, job_id: str) -> int:
        """Increments retry count and sets status back to queued."""
        now = datetime.now(timezone.utc)
        coll = cls._collection()
        res = await coll.find_one_and_update(
            {"job_id": job_id},
            {
                "$inc": {"retry_count": 1},
                "$set": {
                    "status": "queued",
                    "current_stage": "queued",
                    "error": None,
                    "message": "Job queued for retry",
                    "updated_at": now,
                },
            },
            return_document=True,
        )
        return res.get("retry_count", 1) if res else 1

    @classmethod
    async def list_jobs(
        cls,
        owner_hash: Optional[str] = None,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Lists jobs with filtering and pagination."""
        coll = cls._collection()
        query: Dict[str, Any] = {}
        if owner_hash:
            query["user_id"] = owner_hash
        if status and status != "all":
            query["status"] = status

        total = await coll.count_documents(query)
        skip = (max(1, page) - 1) * page_size
        cursor = coll.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(page_size)
        items = await cursor.to_list(length=page_size)
        return items, total

    @classmethod
    async def get_stale_processing_jobs(cls, max_age_seconds: int = 600) -> List[Dict[str, Any]]:
        """Finds jobs stuck in 'processing' state using indexed time cutoff."""
        coll = cls._collection()
        cutoff_epoch = datetime.now(timezone.utc).timestamp() - max_age_seconds
        cutoff_dt = datetime.fromtimestamp(cutoff_epoch, tz=timezone.utc)
        cutoff_iso = cutoff_dt.isoformat()

        # Query using compound index [("status", 1), ("updated_at", 1)]
        cursor = coll.find(
            {
                "status": "processing",
                "$or": [
                    {"updated_at": {"$lt": cutoff_dt}},
                    {"updated_at": {"$lt": cutoff_iso}},
                ],
            },
            {"_id": 0},
        ).limit(100)
        return await cursor.to_list(length=100)

    @classmethod
    async def get_stats(cls) -> Dict[str, Any]:
        """Aggregates real statistics across all jobs using optimized single-pass database queries."""
        coll = cls._collection()

        # Single aggregation group pipeline across collection
        pipeline = [
            {
                "$group": {
                    "_id": "$status",
                    "count": {"$sum": 1},
                    "total_pages": {
                        "$sum": {
                            "$cond": [{"$eq": ["$status", "completed"]}, {"$ifNull": ["$total_pages", 0]}, 0]
                        }
                    },
                    "total_time_ms": {
                        "$sum": {
                            "$cond": [
                                {"$eq": ["$status", "completed"]},
                                {"$ifNull": ["$metadata.processing_time_ms", 0]},
                                0,
                            ]
                        }
                    },
                    "total_retries": {"$sum": {"$ifNull": ["$retry_count", 0]}},
                }
            }
        ]

        try:
            status_groups = await coll.aggregate(pipeline).to_list(length=20)
        except Exception:
            # Fallback if aggregation fails in specific mock environments
            status_groups = []

        counts = {"queued": 0, "processing": 0, "completed": 0, "failed": 0, "cancelled": 0}
        total_pages = 0
        total_time_ms = 0
        total_retries = 0
        total_jobs = 0

        for g in status_groups:
            st = g.get("_id")
            c = g.get("count", 0)
            total_jobs += c
            if st in counts:
                counts[st] = c
            total_pages += g.get("total_pages", 0)
            total_time_ms += g.get("total_time_ms", 0)
            total_retries += g.get("total_retries", 0)

        # If empty (e.g. initial state or mock without aggregation return), calculate directly
        if total_jobs == 0 and not status_groups:
            total_jobs = await coll.count_documents({})
            counts["queued"] = await coll.count_documents({"status": "queued"})
            counts["processing"] = await coll.count_documents({"status": "processing"})
            counts["completed"] = await coll.count_documents({"status": "completed"})
            counts["failed"] = await coll.count_documents({"status": "failed"})
            counts["cancelled"] = await coll.count_documents({"status": "cancelled"})

        completed_count = counts["completed"]
        avg_time = (total_time_ms / completed_count) if completed_count > 0 else 0.0

        # Query up to 10 most recent error entries with light projection
        recent_errors_cursor = coll.find(
            {"error": {"$ne": None}},
            {"_id": 0, "job_id": 1, "error": 1, "updated_at": 1},
        ).sort("updated_at", -1).limit(10)
        recent_errors_docs = await recent_errors_cursor.to_list(length=10)

        recent_errors = [
            {
                "job_id": j.get("job_id"),
                "error": j.get("error"),
                "timestamp": j.get("updated_at"),
            }
            for j in recent_errors_docs
            if j.get("error")
        ]

        return {
            "total_jobs": total_jobs,
            "queued_jobs": counts["queued"],
            "processing_jobs": counts["processing"],
            "completed_jobs": counts["completed"],
            "failed_jobs": counts["failed"],
            "cancelled_jobs": counts["cancelled"],
            "total_pages_processed": total_pages,
            "average_processing_time_ms": round(avg_time, 2),
            "retry_count_total": total_retries,
            "recent_errors": recent_errors,
        }
