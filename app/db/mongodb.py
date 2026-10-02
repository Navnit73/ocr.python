"""
Async MongoDB Database Connector and Lifecycle Manager.
Supports Motor AsyncIO with automated connection pooling, index setup,
and automatic in-memory fallback (mongomock_motor) when MongoDB server is not reachable.
"""

import asyncio
import logging
from typing import Any, Optional
import motor.motor_asyncio
from pymongo.errors import ConnectionFailure, ServerSelectionTimeoutError

from app.core.config import get_settings

logger = logging.getLogger("db.mongodb")


class MongoDBManager:
    """Manages Async Motor client connection, indexes, and failover."""

    client: Optional[Any] = None
    db: Optional[Any] = None
    is_mock: bool = False
    _initialized: bool = False

    @classmethod
    def is_connected(cls) -> bool:
        """Returns True if MongoDB or Mock database is active."""
        return cls.db is not None

    @classmethod
    async def connect(cls, force_mock: bool = False) -> None:
        """Initializes database connection with auto-fallback to mock if offline."""
        if cls._initialized and cls.db is not None:
            return

        settings = get_settings()

        if force_mock or not settings.mongodb_enabled:
            logger.info("Using Async MongoMock in-memory database...")
            cls._init_mock(settings.mongodb_db_name)
            return

        try:
            logger.info(f"Connecting to MongoDB at {settings.mongodb_uri} (db: {settings.mongodb_db_name})...")
            
            client_kwargs: dict[str, Any] = {
                "serverSelectionTimeoutMS": 5000,
                "connectTimeoutMS": 5000,
                "maxPoolSize": 50,
                "minPoolSize": 5,
            }
            try:
                import certifi
                client_kwargs["tlsCAFile"] = certifi.where()
            except ImportError:
                pass

            client = motor.motor_asyncio.AsyncIOMotorClient(
                settings.mongodb_uri,
                **client_kwargs,
            )
            # Test connection
            await client.admin.command("ping")
            cls.client = client
            cls.db = client[settings.mongodb_db_name]
            cls.is_mock = False
            cls._initialized = True
            logger.info("✅ Connected successfully to MongoDB server.")
            await cls._setup_indexes()
        except (ConnectionFailure, ServerSelectionTimeoutError, Exception) as e:
            logger.warning(
                f"⚠️ Could not connect to external MongoDB server ({e}). "
                "Failing over gracefully to Async MongoMock in-memory engine."
            )
            cls._init_mock(settings.mongodb_db_name)

    @classmethod
    def _init_mock(cls, db_name: str) -> None:
        try:
            import mongomock_motor
            cls.client = mongomock_motor.AsyncMongoMockClient()
            cls.db = cls.client[db_name]
            cls.is_mock = True
            cls._initialized = True
            logger.info("✅ In-memory mock MongoDB initialized successfully.")
        except ImportError:
            logger.error("mongomock_motor is not installed. Mock database fallback failed.")
            raise

    @classmethod
    async def _setup_indexes(cls) -> None:
        """Ensures required indexes exist for fast queries and uniqueness."""
        if cls.db is None:
            return

        try:
            # Jobs Collection Indexes
            jobs = cls.db["jobs"]
            await jobs.create_index("job_id", unique=True)
            await jobs.create_index("document_id")
            await jobs.create_index("user_id")
            await jobs.create_index("user_email")
            await jobs.create_index("status")
            await jobs.create_index("created_at")

            # Extractions Collection Indexes (Shared with finlyzer.net frontend)
            extractions = cls.db["extractions"]
            await extractions.create_index("id", unique=True)
            await extractions.create_index("job_id")
            await extractions.create_index("user_email")
            await extractions.create_index("document_type")
            await extractions.create_index("status")
            await extractions.create_index("created_at")

            # Users Collection Indexes (Shared with finlyzer.net frontend)
            users = cls.db["users"]
            await users.create_index("email", unique=True)

            # Documents Collection Indexes
            docs = cls.db["documents"]
            await docs.create_index("document_id", unique=True)
            await docs.create_index("user_id")
            await docs.create_index("user_email")
            await docs.create_index("document_type")
            await docs.create_index("created_at")

            # Webhook Deliveries Collection Indexes
            webhooks = cls.db["webhook_deliveries"]
            await webhooks.create_index("delivery_id", unique=True)
            await webhooks.create_index("job_id")
            await webhooks.create_index("delivered_at")

            logger.info("Database indexes verified.")
        except Exception as e:
            logger.warning(f"Index creation warning: {e}")

    @classmethod
    async def disconnect(cls) -> None:
        """Closes active MongoDB client connection."""
        if cls.client is not None and not cls.is_mock:
            cls.client.close()
            logger.info("Closed MongoDB connection.")
        cls.client = None
        cls.db = None
        cls._initialized = False

    @classmethod
    def get_db(cls) -> Any:
        """Returns active database handle."""
        if cls.db is None:
            # Synchronous lazy mock fallback if not yet initialized
            settings = get_settings()
            cls._init_mock(settings.mongodb_db_name)
        return cls.db
