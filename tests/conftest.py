"""
Pytest Fixtures and Test Environment Setup.
"""

import os
import shutil
import tempfile
from typing import AsyncGenerator, Generator
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
import fakeredis.aioredis
from mongomock_motor import AsyncMongoMockClient

from app.core.config import Settings, get_settings
from app.core.constants import Environment, StorageBackendType
from app.db.mongo import mongo_manager, get_db
from app.db.redis import redis_manager, get_redis
from app.main import app
from app.storage.local_storage import LocalStorageService
from app.dependencies.storage import get_storage_service


@pytest.fixture(scope="session")
def test_temp_dir() -> Generator[str, None, None]:
    """Provides an isolated temporary directory for test storage."""
    temp_dir = tempfile.mkdtemp(prefix="ocr_test_")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture(scope="session")
def test_settings(test_temp_dir: str) -> Settings:
    """Overridden test settings."""
    return Settings(
        app_name="AI Document OCR Test Suite",
        app_env=Environment.TEST,
        debug=True,
        storage_backend=StorageBackendType.LOCAL,
        local_storage_dir=os.path.join(test_temp_dir, "storage"),
        temp_file_dir=os.path.join(test_temp_dir, "temp"),
    )


@pytest_asyncio.fixture(autouse=True)
async def mock_databases(test_settings: Settings) -> AsyncGenerator[None, None]:
    """Automatically mocks MongoDB and Redis managers for testing."""
    # Mock MongoDB
    mock_mongo = AsyncMongoMockClient()
    mongo_manager.client = mock_mongo
    mongo_manager.db = mock_mongo[test_settings.mongo.db_name]

    # Mock Redis
    mock_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    redis_manager.redis = mock_redis

    # Override dependencies
    app.dependency_overrides[get_settings] = lambda: test_settings
    app.dependency_overrides[get_db] = lambda: mongo_manager.db
    app.dependency_overrides[get_redis] = lambda: redis_manager.redis
    app.dependency_overrides[get_storage_service] = lambda: LocalStorageService(settings=test_settings)

    yield

    # Teardown
    app.dependency_overrides.clear()
    await mock_redis.aclose()


@pytest_asyncio.fixture
async def async_client(test_settings: Settings) -> AsyncGenerator[AsyncClient, None]:
    """Async HTTP client for testing API endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
