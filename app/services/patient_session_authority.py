"""Authoritative Redis-backed patient session lifecycle.

A valid patient JWT is necessary but not sufficient for current patient authority.
Every authority-bearing patient JWT must resolve to an active server-side session
whose patient, upstream identity subject, epoch, and expiry match the JWT claims.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any

from app.core.redis import get_async_redis_client as get_redis_client

_SESSION_PREFIX = "nexa:patient_session:"
_EPOCH_PREFIX = "nexa:patient_session_epoch:"


class PatientSessionAuthorityUnavailable(RuntimeError):
    """Raised when current patient-session authority cannot be proven safely."""


def _session_key(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return f"{_SESSION_PREFIX}{digest}"


def _epoch_key(patient_id: str) -> str:
    return f"{_EPOCH_PREFIX}{patient_id}"


async def _maybe_await(value):
    return await value if hasattr(value, "__await__") else value


def _decode_json_object(raw: object) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _parse_epoch(raw: object) -> int | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


async def get_or_create_patient_session_epoch(patient_id: str) -> int:
    """Return the current patient-wide session epoch, creating epoch zero once."""
    redis = get_redis_client()
    key = _epoch_key(patient_id)
    try:
        raw = await _maybe_await(redis.get(key))
        if raw is None:
            await _maybe_await(redis.set(key, "0", nx=True))
            raw = await _maybe_await(redis.get(key))
    except Exception as exc:
        raise PatientSessionAuthorityUnavailable(
            "Patient session authority store is unavailable"
        ) from exc
    epoch = _parse_epoch(raw)
    if epoch is None:
        raise PatientSessionAuthorityUnavailable(
            "Patient session epoch is unavailable or invalid"
        )
    return epoch


async def create_patient_session(
    *,
    patient_id: str,
    supabase_user_id: str,
    session_id: str,
    session_epoch: int,
    issued_at: datetime,
    expires_at: datetime,
) -> None:
    """Persist one active patient session with TTL bounded by the JWT expiry."""
    if (
        issued_at.tzinfo is None
        or issued_at.utcoffset() is None
        or expires_at.tzinfo is None
        or expires_at.utcoffset() is None
    ):
        raise ValueError("patient session timestamps must be timezone-aware")
    ttl_seconds = math.ceil((expires_at - datetime.now(timezone.utc)).total_seconds())
    if ttl_seconds <= 0:
        raise ValueError("patient session expiry must be in the future")
    payload = json.dumps(
        {
            "patient_id": patient_id,
            "supabase_user_id": supabase_user_id,
            "session_epoch": session_epoch,
            "status": "active",
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        await _maybe_await(
            get_redis_client().setex(_session_key(session_id), ttl_seconds, payload)
        )
    except Exception as exc:
        raise PatientSessionAuthorityUnavailable(
            "Patient session authority store is unavailable"
        ) from exc


async def resolve_patient_session_authority(
    claims: dict[str, Any],
) -> dict[str, Any] | None:
    """Resolve JWT claims to current live server-side patient authority."""
    patient_id = claims.get("patient_id")
    supabase_user_id = claims.get("supabase_user_id")
    session_id = claims.get("sid")
    session_epoch = claims.get("session_epoch")
    if (
        not isinstance(patient_id, str)
        or not patient_id
        or not isinstance(supabase_user_id, str)
        or not supabase_user_id
        or not isinstance(session_id, str)
        or len(session_id) < 16
        or not isinstance(session_epoch, int)
        or isinstance(session_epoch, bool)
        or session_epoch < 0
    ):
        return None

    redis = get_redis_client()
    try:
        raw_session = await _maybe_await(redis.get(_session_key(session_id)))
        raw_epoch = await _maybe_await(redis.get(_epoch_key(patient_id)))
    except Exception as exc:
        raise PatientSessionAuthorityUnavailable(
            "Patient session authority store is unavailable"
        ) from exc

    session = _decode_json_object(raw_session)
    current_epoch = _parse_epoch(raw_epoch)
    if session is None or current_epoch is None or current_epoch != session_epoch:
        return None
    if (
        session.get("status") != "active"
        or session.get("patient_id") != patient_id
        or session.get("supabase_user_id") != supabase_user_id
        or session.get("session_epoch") != session_epoch
    ):
        return None
    return session


async def resolve_patient_session_id(
    *, patient_id: str, session_id: str
) -> dict[str, Any] | None:
    """Resolve a session identifier for enrollment-token binding checks."""
    redis = get_redis_client()
    try:
        raw_session = await _maybe_await(redis.get(_session_key(session_id)))
        raw_epoch = await _maybe_await(redis.get(_epoch_key(patient_id)))
    except Exception as exc:
        raise PatientSessionAuthorityUnavailable(
            "Patient session authority store is unavailable"
        ) from exc
    session = _decode_json_object(raw_session)
    current_epoch = _parse_epoch(raw_epoch)
    if session is None or current_epoch is None:
        return None
    if (
        session.get("status") != "active"
        or session.get("patient_id") != patient_id
        or session.get("session_epoch") != current_epoch
    ):
        return None
    return session


async def revoke_patient_session(*, patient_id: str, session_id: str) -> bool:
    """Immediately revoke exactly one patient session."""
    session = await resolve_patient_session_id(
        patient_id=patient_id, session_id=session_id
    )
    if session is None:
        return False
    try:
        deleted = await _maybe_await(get_redis_client().delete(_session_key(session_id)))
    except Exception as exc:
        raise PatientSessionAuthorityUnavailable(
            "Patient session authority store is unavailable"
        ) from exc
    return bool(deleted)


async def revoke_all_patient_sessions(patient_id: str) -> int:
    """Invalidate every current patient session by advancing the authority epoch."""
    try:
        value = await _maybe_await(get_redis_client().incr(_epoch_key(patient_id)))
    except Exception as exc:
        raise PatientSessionAuthorityUnavailable(
            "Patient session authority store is unavailable"
        ) from exc
    epoch = _parse_epoch(value)
    if epoch is None:
        raise PatientSessionAuthorityUnavailable(
            "Patient session epoch could not be advanced"
        )
    return epoch
