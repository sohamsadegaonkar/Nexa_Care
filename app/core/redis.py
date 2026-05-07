"""Upstash Redis utilities for Nexa Care.

This module centralizes Redis connectivity and implements the consent-token storage
used by the Zero-Trust Retrieval Layer.

Uses redis-py with the UPSTASH_REDIS_URL from app.core.config.
"""

from __future__ import annotations

from uuid import uuid4

import redis

from app.core.config import get_redis_config

# Default TTL for consent tokens (30 minutes)
CONSENT_TOKEN_TTL_SECONDS = 30 * 60


def get_redis_client() -> redis.Redis:
    """Create a Redis client from UPSTASH_REDIS_URL.

    Upstash URLs are typically rediss:// (TLS). redis-py will handle this.
    """

    cfg = get_redis_config()
    return redis.from_url(cfg.url, decode_responses=True)


def ping_redis() -> bool:
    """Lightweight Redis health check."""

    client = get_redis_client()
    return bool(client.ping())


def issue_token(masked_internal_id: str, ttl_seconds: int = CONSENT_TOKEN_TTL_SECONDS) -> str:
    """Issue a consent/access token for a masked internal id.

    Stores: token -> masked_internal_id, with TTL.

    Args:
        masked_internal_id: UUID string that links PII vault and clinical shard.
        ttl_seconds: Expiration in seconds. Defaults to 30 minutes.

    Returns:
        token (UUID string)
    """

    token = str(uuid4())
    client = get_redis_client()
    client.set(name=token, value=masked_internal_id, ex=int(ttl_seconds))
    return token


def validate_token(token: str) -> str | None:
    """Validate a token and return the associated masked_internal_id.

    Returns:
        masked_internal_id if valid
        None if expired/missing
    """

    client = get_redis_client()
    value = client.get(token)
    return value if value else None


# Backwards-compatible wrappers (kept for existing imports)
def create_access_token(masked_id: str) -> str:
    return issue_token(masked_internal_id=masked_id, ttl_seconds=CONSENT_TOKEN_TTL_SECONDS)


def get_id_from_token(token: str) -> str | None:
    return validate_token(token)
