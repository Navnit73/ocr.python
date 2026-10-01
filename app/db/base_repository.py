"""
Base Generic Async MongoDB Repository.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Generic, List, Optional, Type, TypeVar
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorCollection, AsyncIOMotorDatabase
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class BaseRepository(Generic[T]):
    """Generic async repository providing standard CRUD operations over a MongoDB collection."""

    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str, model_class: Type[T]) -> None:
        self.db = db
        self.collection_name = collection_name
        self.model_class = model_class

    @property
    def collection(self) -> AsyncIOMotorCollection:
        return self.db[self.collection_name]

    @staticmethod
    def to_object_id(id_str: str) -> Optional[ObjectId]:
        """Safely parses a string ID to an ObjectId."""
        if ObjectId.is_valid(id_str):
            return ObjectId(id_str)
        return None

    async def get_by_id(self, id_str: str) -> Optional[T]:
        """Retrieves a document by string ObjectId or string id."""
        query: Dict[str, Any] = {}
        obj_id = self.to_object_id(id_str)
        if obj_id:
            query = {"$or": [{"_id": obj_id}, {"id": id_str}]}
        else:
            query = {"id": id_str}

        doc = await self.collection.find_one(query)
        if not doc:
            return None
        doc["id"] = str(doc.pop("_id", doc.get("id")))
        return self.model_class.model_validate(doc)

    async def find_one(self, filter_query: Dict[str, Any]) -> Optional[T]:
        """Finds a single document matching the given query filter."""
        doc = await self.collection.find_one(filter_query)
        if not doc:
            return None
        doc["id"] = str(doc.pop("_id", doc.get("id")))
        return self.model_class.model_validate(doc)

    async def list(
        self,
        filter_query: Optional[Dict[str, Any]] = None,
        skip: int = 0,
        limit: int = 50,
        sort_by: str = "created_at",
        sort_order: int = -1,
    ) -> List[T]:
        """Lists documents matching the filter with sorting and pagination."""
        query = filter_query or {}
        cursor = self.collection.find(query).sort(sort_by, sort_order).skip(skip).limit(limit)
        results: List[T] = []
        async for doc in cursor:
            doc["id"] = str(doc.pop("_id", doc.get("id")))
            results.append(self.model_class.model_validate(doc))
        return results

    async def count(self, filter_query: Optional[Dict[str, Any]] = None) -> int:
        """Counts documents matching the filter."""
        return await self.collection.count_documents(filter_query or {})

    async def insert(self, data: Dict[str, Any]) -> str:
        """Inserts a new document and returns its ID string."""
        now = datetime.now(timezone.utc)
        data.setdefault("created_at", now)
        data.setdefault("updated_at", now)
        result = await self.collection.insert_one(data)
        return str(result.inserted_id)

    async def update(self, id_str: str, update_data: Dict[str, Any]) -> bool:
        """Updates a document by ID."""
        obj_id = self.to_object_id(id_str)
        query = {"_id": obj_id} if obj_id else {"id": id_str}

        update_data["updated_at"] = datetime.now(timezone.utc)
        result = await self.collection.update_one(query, {"$set": update_data})
        return result.modified_count > 0

    async def delete(self, id_str: str) -> bool:
        """Deletes a document by ID."""
        obj_id = self.to_object_id(id_str)
        query = {"_id": obj_id} if obj_id else {"id": id_str}
        result = await self.collection.delete_one(query)
        return result.deleted_count > 0
