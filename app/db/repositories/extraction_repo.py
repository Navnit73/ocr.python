"""
MongoDB Extraction Repository for Shared `extractions` Collection.
Persists completed and failed extractions accessible by finlyzer.net frontend Vault.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.db.mongodb import MongoDBManager

logger = logging.getLogger("db.extraction_repo")


class ExtractionRepository:
    """Handles CRUD operations for the shared `extractions` collection."""

    @classmethod
    def _collection(cls):
        db = MongoDBManager.get_db()
        return db["extractions"]

    @classmethod
    async def save_extraction(cls, doc_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Upserts an extraction document into the shared `extractions` collection.
        Matches the schema expected by the finlyzer.net frontend.
        """
        doc_id = doc_data.get("id") or doc_data.get("document_id")
        if not doc_id:
            raise ValueError("Extraction record must have an 'id' or 'document_id'.")

        raw_email = doc_data.get("user_email") or "guest"
        user_email = raw_email.lower().strip()
        actual_pages = int(doc_data.get("pages") or doc_data.get("pages_count") or 1)
        now_iso = datetime.now(timezone.utc).isoformat()

        metadata = dict(doc_data.get("metadata") or {})
        metadata["pages"] = actual_pages
        metadata["user_email"] = user_email
        if "processing_time_ms" not in metadata and "processing_time_ms" in doc_data:
            metadata["processing_time_ms"] = doc_data["processing_time_ms"]
        if "ocr_engine" not in metadata and "ocr_engine" in doc_data:
            metadata["ocr_engine"] = doc_data["ocr_engine"]
        if "ai_cleaned" not in metadata and "clean_with_ai" in doc_data:
            metadata["ai_cleaned"] = doc_data["clean_with_ai"]

        extraction_doc = {
            "id": doc_id,
            "job_id": doc_data.get("job_id"),
            "user_email": user_email,
            "document_type": doc_data.get("document_type", "general"),
            "filename": doc_data.get("filename", "document.pdf"),
            "pages": actual_pages,
            "status": doc_data.get("status", "success"),
            "extraction": doc_data.get("extraction"),
            "raw_text": doc_data.get("raw_text", ""),
            "cleaned_text": doc_data.get("cleaned_text"),
            "metadata": metadata,
            "updated_at": now_iso,
        }

        if "error" in doc_data and doc_data["error"]:
            extraction_doc["error"] = doc_data["error"]
        if "warnings" in doc_data:
            extraction_doc["warnings"] = doc_data["warnings"]

        coll = cls._collection()
        await coll.update_one(
            {"id": doc_id},
            {
                "$set": extraction_doc,
                "$setOnInsert": {"created_at": doc_data.get("created_at") or now_iso},
            },
            upsert=True,
        )
        logger.info(f"Saved extraction to shared 'extractions' collection: {doc_id} (user: {user_email})")
        return extraction_doc

    @classmethod
    async def fail_extraction(
        cls,
        document_id: str,
        job_id: str,
        user_email: Optional[str] = None,
        error_message: str = "Processing failed",
        filename: str = "document.pdf",
        document_type: str = "general",
        pages: int = 1,
    ) -> Dict[str, Any]:
        """Upserts an extraction record in 'error' status upon job or worker failure."""
        normalized_email = (user_email or "guest").lower().strip()
        now_iso = datetime.now(timezone.utc).isoformat()
        error_doc = {
            "id": document_id,
            "job_id": job_id,
            "user_email": normalized_email,
            "document_type": document_type,
            "filename": filename,
            "pages": pages,
            "status": "error",
            "extraction": None,
            "raw_text": "",
            "cleaned_text": "",
            "error": error_message,
            "metadata": {
                "pages": pages,
                "user_email": normalized_email,
                "error": error_message,
            },
            "updated_at": now_iso,
        }
        coll = cls._collection()
        await coll.update_one(
            {"id": document_id},
            {
                "$set": error_doc,
                "$setOnInsert": {"created_at": now_iso},
            },
            upsert=True,
        )
        logger.warning(f"Recorded error status in 'extractions' collection for {document_id}: {error_message}")
        return error_doc

    @classmethod
    async def get_extraction(
        cls,
        document_id: str,
        user_email: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieves extraction by document id, optionally checking user email."""
        coll = cls._collection()
        query: Dict[str, Any] = {"$or": [{"id": document_id}, {"document_id": document_id}]}
        doc = await coll.find_one(query, {"_id": 0})
        if not doc:
            return None
        if user_email and user_email.lower().strip() not in ("guest", "admin"):
            doc_email = doc.get("user_email", "").lower().strip()
            if doc_email and doc_email != user_email.lower().strip():
                return None
        return doc

    @classmethod
    async def list_extractions(
        cls,
        user_email: Optional[str] = None,
        document_type: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Lists extractions with pagination and filtering by user email and document type."""
        coll = cls._collection()
        query: Dict[str, Any] = {}
        if user_email and user_email.lower().strip() != "all":
            query["user_email"] = user_email.lower().strip()
        if document_type and document_type != "all":
            query["document_type"] = document_type

        total = await coll.count_documents(query)
        skip = (max(1, page) - 1) * page_size
        cursor = coll.find(query, {"_id": 0}).sort("created_at", -1).skip(skip).limit(page_size)
        items = await cursor.to_list(length=page_size)
        return items, total
