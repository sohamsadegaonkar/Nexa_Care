"""Upstash Redis client factory for Nexa Care.

This module centralizes Redis connectivity so other components (e.g., consent token
issuance/validation) can reuse it.

Uses redis-py with the UPSTASH_REDIS_URL from app.core.config.
"""

from __future__ import annotations

import redis

from app.core.config import get_redis_config


def get_redis_client() -> redis.Redis:
    """Create a Redis client from UPSTASH_REDIS_URL.

    Upstash URLs are typically rediss:// (TLS). redis-py will handle this.
    """

    cfg = get_redis_config()
    return redis.from_url(cfg.url, decode_responses=True)
