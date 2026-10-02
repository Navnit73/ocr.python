"""
MongoDB Document Repository for Persistent Extractions and History Retrieval.
"""

from datetime import datetime, timezone
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from app.db.mongodb import MongoDBManager

logger = logging.getLogger("db.document_repo")


class DocumentRepository:
    """Handles persistence and retrieval of extracted document records."""

    @classmethod
    def _collection(cls):
        db = MongoDBManager.get_db()
        return db["documents"]

    @classmethod
    async def save_document(cls, doc_data: Dict[str, Any]) -> Dict[str, Any]:
        """Saves a document extraction record into MongoDB."""
        now = datetime.now(timezone.utc)
        record = {
            "document_id": doc_data["document_id"],
            "job_id": doc_data.get("job_id"),
            "user_id": doc_data.get("user_id"),
            "filename": doc_data.get("filename", "document.pdf"),
            "content_type": doc_data.get("content_type", "application/pdf"),
            "file_size_bytes": doc_data.get("file_size_bytes", 0),
            "document_type": doc_data.get("document_type", "general"),
            "status": doc_data.get("status", "success"),
            "pages_count": doc_data.get("pages_count", 1),
            "extraction": doc_data.get("extraction"),
            "raw_text": doc_data.get("raw_text", ""),
            "cleaned_text": doc_data.get("cleaned_text"),
            "pages": doc_data.get("pages", []),
            "metadata": doc_data.get("metadata", {}),
            "warnings": doc_data.get("warnings", []),
            "created_at": doc_data.get("created_at", now),
            "updated_at": doc_data.get("updated_at", now),
        }
        coll = cls._collection()
        # Upsert by document_id
        await coll.update_one(
            {"document_id": record["document_id"]},
            {"$set": record},
            upsert=True,
        )
        logger.info(f"Saved document extraction record: {record['document_id']}")
        return record

    @classmethod
    async def get_document(
        cls,
        document_id: str,
        owner_hash: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Retrieves complete document record by ID, verifying owner hash if provided."""
        coll = cls._collection()
        doc = await coll.find_one({"document_id": document_id}, {"_id": 0})
        if not doc:
            return None
        if owner_hash and doc.get("user_id") and doc.get("user_id") != owner_hash:
            return None
        return doc

    @classmethod
    async def list_documents(
        cls,
        owner_hash: Optional[str] = None,
        document_type: Optional[str] = None,
        search: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Lists document extraction records with filtering, keyword search, and pagination."""
        coll = cls._collection()
        query: Dict[str, Any] = {}
        if owner_hash:
            query["user_id"] = owner_hash
        if document_type and document_type != "all":
            query["document_type"] = document_type
        if search:
            escaped = re.escape(search)
            query["$or"] = [
                {"filename": {"$regex": escaped, "$options": "i"}},
                {"document_id": {"$regex": escaped, "$options": "i"}},
                {"raw_text": {"$regex": escaped, "$options": "i"}},
            ]

        total = await coll.count_documents(query)
        skip = (max(1, page) - 1) * page_size
        cursor = coll.find(
            query,
            {
                "_id": 0,
                "document_id": 1,
                "job_id": 1,
                "filename": 1,
                "content_type": 1,
                "file_size_bytes": 1,
                "document_type": 1,
                "status": 1,
                "pages_count": 1,
                "extraction": 1,
                "created_at": 1,
                "metadata": 1,
            },
        ).sort("created_at", -1).skip(skip).limit(page_size)

        raw_items = await cursor.to_list(length=page_size)
        items = []
        for r in raw_items:
            # Format item for DocumentListItem schema
            items.append({
                "id": r.get("document_id"),
                "job_id": r.get("job_id"),
                "filename": r.get("filename", "document.pdf"),
                "content_type": r.get("content_type", "application/pdf"),
                "file_size_bytes": r.get("file_size_bytes", 0),
                "document_type": r.get("document_type", "general"),
                "status": r.get("status", "success"),
                "pages_count": r.get("pages_count", 1),
                "summary": r.get("extraction"),
                "created_at": r.get("created_at"),
                "metadata": r.get("metadata", {}),
            })
        return items, total

    @classmethod
    async def delete_document(
        cls,
        document_id: str,
        owner_hash: Optional[str] = None,
    ) -> bool:
        """Deletes a document from MongoDB if caller owns it."""
        coll = cls._collection()
        query: Dict[str, Any] = {"document_id": document_id}
        if owner_hash:
            query["user_id"] = owner_hash
        res = await coll.delete_one(query)
        return res.deleted_count > 0
