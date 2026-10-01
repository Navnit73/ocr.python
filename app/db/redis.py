"""
Redis Async Connection Pool Lifecycle and Utility Operations.
"""

from typing import Optional
import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError

from app.core.config import Settings
from app.core.logging import logger


class RedisManager:
    """Manages Redis Async connection pool and operations."""

    def __init__(self) -> None:
        self.redis: Optional[aioredis.Redis] = None

    async def connect(self, settings: Settings) -> None:
        """Initializes Redis async connection pool."""
        try:
            logger.info(f"Connecting to Redis at {settings.redis.url}...")
            self.redis = aioredis.from_url(
                settings.redis.url,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=5,
            )
            # Verify connectivity with ping
            await self.redis.ping()
            logger.info("Connected to Redis successfully.")
        except RedisConnectionError as exc:
            logger.error(f"Redis connection failed: {exc}")
        except Exception as exc:
            logger.error(f"Unexpected error connecting to Redis: {exc}")

    async def disconnect(self) -> None:
        """Closes the Redis connection pool."""
        if self.redis:
            logger.info("Closing Redis connection pool...")
            await self.redis.close()
            self.redis = None
            logger.info("Redis connection closed.")

    async def ping(self) -> bool:
        """Pings Redis to check connectivity status."""
        if not self.redis:
            return False
        try:
            res = await self.redis.ping()
            return bool(res)
        except Exception as exc:
            logger.warning(f"Redis ping check failed: {exc}")
            return False

    def get_client(self) -> aioredis.Redis:
        """Returns the active Redis client."""
        if self.redis is None:
            raise RuntimeError("Redis is not connected. Ensure lifespan startup was called.")
        return self.redis


# Global singleton instance
redis_manager = RedisManager()


async def get_redis() -> aioredis.Redis:
    """FastAPI dependency for obtaining the async Redis client."""
    return redis_manager.get_client()
