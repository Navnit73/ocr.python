"""
In-Memory Sliding Window Rate Limiter for DDoS & Resource Abuse Prevention.
"""

import time
from collections import defaultdict
import threading
from typing import Dict, List
from fastapi import HTTPException, Request, status

from app.core.config import get_settings
from app.core.security import hash_key


class SlidingWindowRateLimiter:
    """Thread-safe In-Memory Sliding Window Rate Limiter."""

    def __init__(self):
        self._lock = threading.Lock()
        self._requests: Dict[str, List[float]] = defaultdict(list)
        self._last_cleanup: float = time.time()
        self._cleanup_interval: float = 300.0  # Clean every 5 minutes

    def check_rate_limit(self, client_identifier: str) -> None:
        """
        Checks if the request exceeds the allowed rate limit.
        Raises HTTPException(429) if exceeded.
        """
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return

        now = time.time()
        window_seconds = 60.0
        max_requests = settings.rate_limit_requests_per_minute

        with self._lock:
            # Perform periodic cleanup of stale clients
            if now - self._last_cleanup > self._cleanup_interval:
                self._cleanup_stale(now, window_seconds)

            timestamps = self._requests[client_identifier]

            # Filter out timestamps older than the sliding window
            cutoff = now - window_seconds
            valid_timestamps = [t for t in timestamps if t > cutoff]
            self._requests[client_identifier] = valid_timestamps

            if len(valid_timestamps) >= max_requests:
                oldest = valid_timestamps[0]
                retry_after = max(1, int(oldest + window_seconds - now))
                reset_time = int(oldest + window_seconds)

                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded ({max_requests} requests/min). Please retry after {retry_after} seconds.",
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(max_requests),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(reset_time),
                    },
                )

            # Record current request timestamp
            self._requests[client_identifier].append(now)

    def _cleanup_stale(self, now: float, window_seconds: float) -> None:
        """Removes entries that have no requests within the sliding window."""
        cutoff = now - window_seconds
        stale_keys = [k for k, times in self._requests.items() if not times or times[-1] <= cutoff]
        for k in stale_keys:
            del self._requests[k]
        self._last_cleanup = now

    def reset(self) -> None:
        """Clears all rate limit records."""
        with self._lock:
            self._requests.clear()


# Singleton instance
_limiter = SlidingWindowRateLimiter()


def get_rate_limiter() -> SlidingWindowRateLimiter:
    """Returns the global rate limiter instance."""
    return _limiter


async def check_rate_limit(request: Request) -> None:
    """
    FastAPI dependency that enforces rate limits per API key header, Bearer token, or IP address.
    """
    header_key = request.headers.get("X-API-Key")
    auth_header = request.headers.get("Authorization")

    if header_key and header_key.strip():
        identifier = f"key:{hash_key(header_key.strip())}"
    elif auth_header and auth_header.strip().startswith("Bearer "):
        token = auth_header.strip()[7:].strip()
        identifier = f"key:{hash_key(token)}"
    elif request.client and request.client.host:
        identifier = f"ip:{request.client.host}"
    else:
        identifier = "anonymous"

    _limiter.check_rate_limit(identifier)
