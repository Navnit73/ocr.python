"""
Pytest configuration and global test fixtures.
"""

import asyncio
import pytest
import pytest_asyncio

from app.db.mongodb import MongoDBManager
from app.services.deepseek_client import DeepSeekClient
from app.services.event_bus import JobEventBus
from app.workers.worker_manager import WorkerManager


@pytest_asyncio.fixture(autouse=True)
async def cleanup_resources_after_each_test():
    """Ensures background worker pool and clients are cleanly torn down after every test."""
    yield
    try:
        await WorkerManager.stop()
    except Exception:
        pass
    try:
        await DeepSeekClient.close_client()
    except Exception:
        pass
    JobEventBus.reset()
