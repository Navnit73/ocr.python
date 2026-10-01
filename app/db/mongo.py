"""
MongoDB Async Database Driver (Motor) Lifecycle and Connection Management.
"""

from typing import Optional
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from app.core.config import Settings
from app.core.logging import logger


class MongoManager:
    """Manages MongoDB Async client connection and database handle."""

    def __init__(self) -> None:
        self.client: Optional[AsyncIOMotorClient] = None
        self.db: Optional[AsyncIOMotorDatabase] = None

    async def connect(self, settings: Settings) -> None:
        """Initializes Motor Async client and connects to MongoDB."""
        try:
            logger.info(f"Connecting to MongoDB at {settings.mongo.uri}...")
            self.client = AsyncIOMotorClient(
                settings.mongo.uri,
                minPoolSize=settings.mongo.min_pool_size,
                maxPoolSize=settings.mongo.max_pool_size,
                serverSelectionTimeoutMS=settings.mongo.timeout_ms,
            )
            self.db = self.client[settings.mongo.db_name]

            # Trigger a ping command to verify connection
            await self.client.admin.command("ping")
            logger.info(f"Connected to MongoDB database: '{settings.mongo.db_name}' successfully.")
        except (ConnectionFailure, ServerSelectionTimeoutError) as exc:
            logger.error(f"MongoDB connection failed: {exc}")
            # Keep client initialized for retries/graceful startup in non-blocking mode if needed
        except Exception as exc:
            logger.error(f"Unexpected error connecting to MongoDB: {exc}")

    async def disconnect(self) -> None:
        """Closes the MongoDB connection pool."""
        if self.client:
            logger.info("Closing MongoDB connection pool...")
            self.client.close()
            self.client = None
            self.db = None
            logger.info("MongoDB connection closed.")

    async def ping(self) -> bool:
        """Pings the database to verify live connectivity."""
        if not self.client:
            return False
        try:
            await self.client.admin.command("ping")
            return True
        except Exception as exc:
            logger.warning(f"MongoDB ping check failed: {exc}")
            return False

    def get_database(self) -> AsyncIOMotorDatabase:
        """Returns the active MongoDB database instance."""
        if self.db is None:
            raise RuntimeError("Database is not connected. Ensure lifespan startup was called.")
        return self.db


# Global singleton instance
mongo_manager = MongoManager()


async def get_db() -> AsyncIOMotorDatabase:
    """FastAPI dependency for obtaining the async database handle."""
    return mongo_manager.get_database()
