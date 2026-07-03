"""Simple per-key rate limiter for FastAPI routes.

Uses an in-memory fixed-window counter. Good enough for an MVP and single
worker; for multi-worker production deployments, swap this for a Redis-backed
limiter (e.g., slowapi) so all workers share the same counter.
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable

from fastapi import HTTPException, Request, status


@dataclass
class _RateLimitBucket:
    window_start: float
    count: int = 0


class RateLimiter:
    """Fixed-window counter rate limiter.

    Attributes:
        max_requests: maximum allowed requests per window.
        window_seconds: window duration in seconds.
        key_func: function Request -> str used to bucket requests.
    """

    def __init__(
        self,
        max_requests: int,
        window_seconds: int,
        key_func: Callable[[Request], str],
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.key_func = key_func
        self._buckets: dict[str, _RateLimitBucket] = defaultdict(
            lambda: _RateLimitBucket(window_start=time.monotonic())
        )

    def _current_bucket(self, key: str) -> _RateLimitBucket:
        bucket = self._buckets[key]
        now = time.monotonic()
        if now - bucket.window_start > self.window_seconds:
            bucket = _RateLimitBucket(window_start=now)
            self._buckets[key] = bucket
        return bucket

    def is_allowed(self, key: str) -> bool:
        bucket = self._current_bucket(key)
        if bucket.count >= self.max_requests:
            return False
        bucket.count += 1
        return True

    async def __call__(self, request: Request) -> None:
        key = self.key_func(request)
        if not self.is_allowed(key):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
            )


def client_ip_key(request: Request) -> str:
    """Bucket by the request's client IP (or a forwarded header if present)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
