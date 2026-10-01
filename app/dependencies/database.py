"""
FastAPI Database & Cache Dependencies.
"""

from typing import AsyncGenerator
from fastapi import Depends
from motor.motor_asyncio import AsyncIOMotorDatabase
import redis.asyncio as aioredis

from app.db.mongo import mongo_manager
from app.db.redis import redis_manager


async def get_db() -> AsyncIOMotorDatabase:
    """Dependency provider for async MongoDB database instance."""
    return mongo_manager.get_database()


async def get_redis() -> aioredis.Redis:
    """Dependency provider for async Redis client."""
    return redis_manager.get_client()
