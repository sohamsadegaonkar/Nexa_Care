"""Routine Redis capability state machine for scoped reconstruction reads."""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import redis.asyncio as redis_async

from app.core.config import get_redis_config

ROUTINE_CONSENT_TTL_SECONDS = 30 * 60
ROUTINE_CONSENT_PREFIX = "nexa:routine_consent:"


class RoutineConsentUnavailable(RuntimeError):
    """Raised when the routine consent Redis store cannot be reached."""


@dataclass(frozen=True, slots=True)
class RoutineConsentCapability:
    patient_id: str
    clinician_id: str
    purpose: str
    scope: list[str]
    nonce: str
    ttl: int
    issued_at: str


@lru_cache(maxsize=1)
def get_routine_redis_client() -> redis_async.Redis:
    """Create a process-wide async Redis client for routine capabilities."""

    cfg = get_redis_config()
    return redis_async.from_url(cfg.url, decode_responses=True)


def _token_key(token: str) -> str:
    return token if token.startswith(ROUTINE_CONSENT_PREFIX) else f"{ROUTINE_CONSENT_PREFIX}{token}"


def _parse_payload(raw_value: object) -> RoutineConsentCapability | None:
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
    issued_at = payload.get("issued_at")

    if not all(isinstance(value, str) and value for value in [patient_id, clinician_id, purpose, nonce, issued_at]):
        return None
    if not isinstance(scope, list) or not all(isinstance(item, str) and item.strip() for item in scope):
        return None
    if not isinstance(ttl, int) or ttl <= 0:
        return None

    return RoutineConsentCapability(
        patient_id=patient_id,
        clinician_id=clinician_id,
        purpose=purpose,
        scope=[item.strip() for item in scope],
        nonce=nonce,
        ttl=ttl,
        issued_at=issued_at,
    )


def _matches(capability: RoutineConsentCapability, patient_id: str, clinician_id: str, purpose: str) -> bool:
    return (
        capability.patient_id == patient_id
        and capability.clinician_id == clinician_id
        and capability.purpose == purpose
    )


async def issue(
    *,
    patient_id: str,
    clinician_id: str,
    purpose: str,
    scope: list[str],
    ttl: int = ROUTINE_CONSENT_TTL_SECONDS,
    nonce: str | None = None,
) -> str:
    """Issue one routine Redis capability bound to patient, clinician, purpose, and scope."""

    clean_scope = [item.strip() for item in scope if isinstance(item, str) and item.strip()]
    if not clean_scope:
        raise ValueError("Routine consent scope must contain at least one field.")
    if ttl <= 0:
        raise ValueError("Routine consent ttl must be positive.")

    token = f"{ROUTINE_CONSENT_PREFIX}{secrets.token_urlsafe(32)}"
    payload = {
        "patient_id": patient_id,
        "clinician_id": clinician_id,
        "purpose": purpose,
        "scope": clean_scope,
        "nonce": nonce or secrets.token_urlsafe(16),
        "ttl": int(ttl),
        "issued_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        redis_client = get_routine_redis_client()
        await redis_client.set(token, json.dumps(payload, sort_keys=True), ex=int(ttl))
    except Exception as exc:
        raise RoutineConsentUnavailable("Routine consent store is unavailable.") from exc

    return token


async def validate(
    *,
    token: str,
    patient_id: str,
    clinician_id: str,
    purpose: str,
) -> RoutineConsentCapability | None:
    """Validate a live routine capability, failing closed on malformed or mismatched state."""

    try:
        redis_client = get_routine_redis_client()
        raw_value = await redis_client.get(_token_key(token))
    except Exception as exc:
        raise RoutineConsentUnavailable("Routine consent store is unavailable.") from exc

    capability = _parse_payload(raw_value)
    if capability is None or not _matches(capability, patient_id, clinician_id, purpose):
        return None

    return capability


async def consume(
    *,
    token: str,
    patient_id: str,
    clinician_id: str,
    purpose: str,
) -> RoutineConsentCapability | None:
    """Atomically consume a single-use routine capability after reconstruction."""

    try:
        redis_client = get_routine_redis_client()
        raw_value = await redis_client.getdel(_token_key(token))
    except Exception as exc:
        raise RoutineConsentUnavailable("Routine consent store is unavailable.") from exc

    capability = _parse_payload(raw_value)
    if capability is None or not _matches(capability, patient_id, clinician_id, purpose):
        return None

    return capability
