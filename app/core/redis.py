"""Upstash Redis utilities for Nexa Care.

This module centralizes Redis connectivity and implements the consent-token storage
used by the Zero-Trust Retrieval Layer.

Uses redis-py with the UPSTASH_REDIS_URL from app.core.config.
"""

from __future__ import annotations

from uuid import uuid4

import redis

from app.core.config import get_redis_config

# Strict TTL for consent tokens (30 minutes)
CONSENT_TOKEN_TTL_SECONDS = 30 * 60


def get_redis_client() -> redis.Redis:
    """Create a Redis client from UPSTASH_REDIS_URL.

    Upstash URLs are typically rediss:// (TLS). redis-py will handle this.
    """

    cfg = get_redis_config()
    return redis.from_url(cfg.url, decode_responses=True)


def create_access_token(masked_id: str) -> str:
    """Create a time-bound access token for a masked internal id.

    - Generates a random UUID token
    - Stores: token -> masked_id
    - Applies a strict 30-minute TTL
    """

    token = str(uuid4())
    client = get_redis_client()
    # SET token value EX seconds
    client.set(name=token, value=masked_id, ex=CONSENT_TOKEN_TTL_SECONDS)
    return token


def get_id_from_token(token: str) -> str | None:
    """Resolve masked_id from an access token.

    Returns:
        masked_id if token exists (not expired)
        None if missing/expired
    """

    client = get_redis_client()
    value = client.get(token)
    return value if value else None
