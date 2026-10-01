"""
In-Memory TTL Result Cache with Scoped Ownership Verification to Prevent IDOR.
"""

import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple


class ResultCache:
    """Thread-safe In-Memory LRU & TTL cache with API key ownership enforcement."""

    # Key -> (expires_at, owner_hash, value)
    _cache: OrderedDict[str, Tuple[float, Optional[str], Dict[str, Any]]] = OrderedDict()
    _ttl_seconds: int = 3600  # 1 hour retention
    _max_entries: int = 1000

    @classmethod
    def set(
        cls,
        key: str,
        value: Dict[str, Any],
        owner_hash: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        """Stores a result in the cache with expiry timestamp and owner hash."""
        if not key:
            return

        cls._cleanup_expired()

        ttl = ttl_seconds if ttl_seconds is not None else cls._ttl_seconds
        expires_at = time.time() + ttl

        # Evict oldest entry if max capacity reached
        if len(cls._cache) >= cls._max_entries:
            cls._cache.popitem(last=False)

        cls._cache[key] = (expires_at, owner_hash, value)
        cls._cache.move_to_end(key)

    @classmethod
    def get(cls, key: str, caller_hash: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Retrieves a result if not expired and caller is verified owner.
        If caller_hash is provided and does not match owner_hash, returns None to prevent IDOR.
        """
        if not key or key not in cls._cache:
            return None

        expires_at, owner_hash, value = cls._cache[key]
        if time.time() > expires_at:
            del cls._cache[key]
            return None

        # Verify ownership if owner_hash and caller_hash are present
        if owner_hash and caller_hash and owner_hash != caller_hash:
            return None

        cls._cache.move_to_end(key)
        return value

    @classmethod
    def get_with_owner(cls, key: str) -> Optional[Tuple[Optional[str], Dict[str, Any]]]:
        """Returns (owner_hash, value) if not expired."""
        if not key or key not in cls._cache:
            return None

        expires_at, owner_hash, value = cls._cache[key]
        if time.time() > expires_at:
            del cls._cache[key]
            return None

        cls._cache.move_to_end(key)
        return (owner_hash, value)

    @classmethod
    def delete(cls, key: str, caller_hash: Optional[str] = None) -> bool:
        """Deletes a key from cache if caller is owner."""
        if key in cls._cache:
            _, owner_hash, _ = cls._cache[key]
            if owner_hash and caller_hash and owner_hash != caller_hash:
                return False
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
        expired_keys = [k for k, (exp, _, _) in cls._cache.items() if now > exp]
        for k in expired_keys:
            del cls._cache[k]
