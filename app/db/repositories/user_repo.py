"""
MongoDB User Repository for Page Quota Reduction and Usage Tracking.
Updates `pages_processed` in the shared `users` collection.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional

from app.db.mongodb import MongoDBManager

logger = logging.getLogger("db.user_repo")


class UserRepository:
    """Handles operations on the shared `users` collection."""

    @classmethod
    def _collection(cls):
        db = MongoDBManager.get_db()
        return db["users"]

    @classmethod
    async def increment_pages_processed(cls, user_email: Optional[str], pages_count: int) -> bool:
        """
        Increments the `pages_processed` field in the `users` collection for the given user email.
        Ignores guest or empty emails.
        """
        if not user_email:
            return False

        normalized_email = user_email.lower().strip()
        if normalized_email in ("", "guest", "none", "null"):
            return False

        if pages_count <= 0:
            return False

        now_iso = datetime.now(timezone.utc).isoformat()
        coll = cls._collection()
        res = await coll.update_one(
            {"email": normalized_email},
            {
                "$inc": {"pages_processed": pages_count},
                "$set": {"updated_at": now_iso},
            },
            upsert=True,
        )
        logger.info(f"Incremented pages_processed by {pages_count} for user: {normalized_email}")
        return res.modified_count > 0 or res.upserted_id is not None or res.matched_count > 0

    @classmethod
    async def get_user(cls, user_email: str) -> Optional[Dict[str, Any]]:
        """Fetches user document by email from the users collection."""
        if not user_email:
            return None
        normalized_email = user_email.lower().strip()
        coll = cls._collection()
        return await coll.find_one({"email": normalized_email}, {"_id": 0})
