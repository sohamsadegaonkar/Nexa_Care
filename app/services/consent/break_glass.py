"""Break-glass Redis capability state machine.

This module intentionally does not reuse routine consent business logic. Emergency
access has a separate Redis namespace, mandatory compliance notification, and an
immutable reason code stored at issue time.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import redis.asyncio as redis_async

from app.core.config import get_redis_config

BREAK_GLASS_TTL_SECONDS = 15 * 60
BREAK_GLASS_PREFIX = "nexa:break_glass:"
COMPLIANCE_QUEUE_KEY = "nexa:compliance_queue:break_glass"


class BreakGlassUnavailable(RuntimeError):
    """Raised when break-glass Redis state or notification cannot be persisted."""


@dataclass(frozen=True, slots=True)
class BreakGlassCapability:
    patient_id: str
    clinician_id: str
    purpose: str
    scope: list[str]
    nonce: str
    ttl: int
    reason_code: str
    issued_at: str


@lru_cache(maxsize=1)
def get_break_glass_redis_client() -> redis_async.Redis:
    """Create a process-wide async Redis client for break-glass capabilities."""

    cfg = get_redis_config()
    return redis_async.from_url(cfg.url, decode_responses=True)


def _token_key(token: str) -> str:
    return token if token.startswith(BREAK_GLASS_PREFIX) else f"{BREAK_GLASS_PREFIX}{token}"


def _parse_payload(raw_value: object) -> BreakGlassCapability | None:
    if not raw_value:
        return None

    if isinstance(raw_value, bytes):
        raw_value = raw_value.decode("utf-8")

    try:
        payload: Any = json.loads(raw_value)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(payload, dict):
        return None

    patient_id = payload.get("patient_id")
    clinician_id = payload.get("clinician_id")
    purpose = payload.get("purpose")
    scope = payload.get("scope")
    nonce = payload.get("nonce")
    ttl = payload.get("ttl")
    reason_code = payload.get("reason_code")
    issued_at = payload.get("issued_at")

    required_strings = [patient_id, clinician_id, purpose, nonce, reason_code, issued_at]
    if not all(isinstance(value, str) and value for value in required_strings):
        return None
    if not isinstance(scope, list) or not all(isinstance(item, str) and item.strip() for item in scope):
        return None
    if not isinstance(ttl, int) or ttl <= 0:
        return None

    return BreakGlassCapability(
        patient_id=patient_id,
        clinician_id=clinician_id,
        purpose=purpose,
        scope=[item.strip() for item in scope],
        nonce=nonce,
        ttl=ttl,
        reason_code=reason_code,
        issued_at=issued_at,
    )


async def issue(
    *,
    patient_id: str,
    clinician_id: str,
    purpose: str,
    scope: list[str],
    reason_code: str,
    ttl: int = BREAK_GLASS_TTL_SECONDS,
    nonce: str | None = None,
) -> str:
    """Issue an emergency capability and notify the compliance queue atomically enough to fail closed."""

    clean_scope = [item.strip() for item in scope if isinstance(item, str) and item.strip()]
    if not clean_scope:
        raise ValueError("Break-glass scope must contain at least one field.")
    if ttl <= 0:
        raise ValueError("Break-glass ttl must be positive.")
    if not reason_code.strip():
        raise ValueError("Break-glass reason_code is required.")

    token = f"{BREAK_GLASS_PREFIX}{secrets.token_urlsafe(32)}"
    issued_at = datetime.now(timezone.utc).isoformat()
    payload = {
        "patient_id": patient_id,
        "clinician_id": clinician_id,
        "purpose": purpose,
        "scope": clean_scope,
        "nonce": nonce or secrets.token_urlsafe(16),
        "ttl": int(ttl),
        "reason_code": reason_code.strip(),
        "issued_at": issued_at,
    }
    notification = {
        "token": token,
        "patient_id": patient_id,
        "clinician_id": clinician_id,
        "purpose": purpose,
        "reason_code": reason_code.strip(),
        "issued_at": issued_at,
    }

    try:
        redis_client = get_break_glass_redis_client()
        await redis_client.set(token, json.dumps(payload, sort_keys=True), ex=int(ttl))
        await redis_client.rpush(COMPLIANCE_QUEUE_KEY, json.dumps(notification, sort_keys=True))
    except Exception as exc:
        try:
            redis_client = get_break_glass_redis_client()
            await redis_client.delete(token)
        except Exception:
            pass
        raise BreakGlassUnavailable("Break-glass capability or compliance notification failed.") from exc

    return token


async def validate(
    *,
    token: str,
    patient_id: str,
    clinician_id: str,
    purpose: str,
) -> BreakGlassCapability | None:
    """Validate a live break-glass capability without consuming it."""

    try:
        redis_client = get_break_glass_redis_client()
        raw_value = await redis_client.get(_token_key(token))
    except Exception as exc:
        raise BreakGlassUnavailable("Break-glass store is unavailable.") from exc

    capability = _parse_payload(raw_value)
    if capability is None:
        return None

    if not (
        capability.patient_id == patient_id
        and capability.clinician_id == clinician_id
        and capability.purpose == purpose
    ):
        return None

    return capability
