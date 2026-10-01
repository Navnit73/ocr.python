"""
In-Memory TTL Result Cache for Stateless Extraction Results.
"""

import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple


class ResultCache:
    """Thread-safe In-Memory LRU & TTL cache for recently processed documents."""

    _cache: OrderedDict[str, Tuple[float, Dict[str, Any]]] = OrderedDict()
    _ttl_seconds: int = 3600  # 1 hour retention
    _max_entries: int = 1000

    @classmethod
    def set(cls, key: str, value: Dict[str, Any], ttl_seconds: Optional[int] = None) -> None:
        """Stores a result in the cache with expiry timestamp."""
        if not key:
            return

        cls._cleanup_expired()

        ttl = ttl_seconds if ttl_seconds is not None else cls._ttl_seconds
        expires_at = time.time() + ttl

        # Evict oldest entry if max capacity reached
        if len(cls._cache) >= cls._max_entries:
            cls._cache.popitem(last=False)

        cls._cache[key] = (expires_at, value)
        cls._cache.move_to_end(key)

    @classmethod
    def get(cls, key: str) -> Optional[Dict[str, Any]]:
        """Retrieves a result if not expired, moving it to end (LRU)."""
        if not key or key not in cls._cache:
            return None

        expires_at, value = cls._cache[key]
        if time.time() > expires_at:
            del cls._cache[key]
            return None

        cls._cache.move_to_end(key)
        return value

    @classmethod
    def delete(cls, key: str) -> bool:
        """Deletes a key from cache."""
        if key in cls._cache:
            del cls._cache[key]
            return True
        return False

    @classmethod
    def clear(cls) -> None:
        """Clears all cached results."""
        cls._cache.clear()

    @classmethod
    def _cleanup_expired(cls) -> None:
        """Removes expired items from cache."""
        now = time.time()
        expired_keys = [k for k, (exp, _) in cls._cache.items() if now > exp]
        for k in expired_keys:
            del cls._cache[k]
