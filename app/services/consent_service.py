"""Redis-backed routine consent service for Nexa Care V2."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from functools import lru_cache

import redis.asyncio as redis_async

from app.core.config import get_redis_config

ROUTINE_CONSENT_TTL_SECONDS = 60 * 60
_CONSENT_TOKEN_PREFIX = "nexa_cons_"


class ConsentServiceUnavailable(RuntimeError):
    """Raised when routine consent cannot be granted due to Redis failure."""


@lru_cache(maxsize=1)
def get_async_redis_client() -> redis_async.Redis:
    """Create a process-wide async Redis client from UPSTASH_REDIS_URL."""

    cfg = get_redis_config()
    return redis_async.from_url(cfg.url, decode_responses=True)


async def grant_routine_consent(patient_id: str, provider_uid: str) -> str:
    """Grant one provider routine access to one patient for one hour.

    Stores token -> JSON(patient_id, provider_uid, granted_at) with a strict
    Redis TTL. If Redis is unavailable, no token is returned.
    """

    token = f"{_CONSENT_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    payload = json.dumps({
        "patient_id": patient_id,
        "provider_uid": provider_uid,
        "granted_at": datetime.now(timezone.utc).isoformat(),
    })

    try:
        redis_client = get_async_redis_client()
        await redis_client.set(token, payload, ex=ROUTINE_CONSENT_TTL_SECONDS)
    except Exception as exc:
        raise ConsentServiceUnavailable("Routine consent store is unavailable.") from exc

    return token


async def verify_routine_consent(token: str, patient_id: str, provider_uid: str) -> bool:
    """Return True only when a live token matches the patient and provider.

    Redis outages, malformed JSON, missing keys, and mismatches all fail closed.
    """

    try:
        redis_client = get_async_redis_client()
        raw_value = await redis_client.get(token)
    except Exception:
        return False

    if not raw_value:
        return False

    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode("utf-8")

    try:
        payload = json.loads(raw_value)
    except (json.JSONDecodeError, TypeError):
        return False

    if not isinstance(payload, dict):
        return False

    return (
        payload.get("patient_id") == patient_id
        and payload.get("provider_uid") == provider_uid
    )


async def revoke_routine_consent(token: str) -> None:
    """Best-effort cleanup for grants that cannot be audited."""

    try:
        redis_client = get_async_redis_client()
        await redis_client.delete(token)
    except Exception:
        pass
