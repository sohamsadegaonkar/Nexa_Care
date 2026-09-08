"""Server-authoritative patient session lifecycle for phone-OTP JWTs.

A valid JWT is necessary but not sufficient patient authority. Every issued
patient JWT must also have a live server-side Redis session record. Session
revocation deletes that authority without waiting for JWT expiry.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

from app.core.redis import get_async_redis_client

PATIENT_SESSION_PREFIX = "nexa:patient_session:"
PATIENT_SESSION_INDEX_PREFIX = "nexa:patient_session_index:"


class PatientSessionStoreUnavailable(RuntimeError):
    """Raised when authoritative patient-session state cannot be trusted."""


@dataclass(frozen=True, slots=True)
class PatientSession:
    patient_id: str
    supabase_user_id: str
    auth_method: str
    jti_digest: str
    issued_at: int
    expires_at: int

    @property
    def session_binding(self) -> str:
        """Opaque binding used to tie downstream grants to this exact session."""
        return self.jti_digest


def _session_digest(jti: str) -> str:
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


def _session_key(jti_digest: str) -> str:
    return f"{PATIENT_SESSION_PREFIX}{jti_digest}"


def _index_key(patient_id: str) -> str:
    patient_digest = hashlib.sha256(patient_id.encode("utf-8")).hexdigest()
    return f"{PATIENT_SESSION_INDEX_PREFIX}{patient_digest}"


async def _maybe_await(value):
    return await value if hasattr(value, "__await__") else value


def _record_from_claims(claims: dict[str, Any]) -> PatientSession | None:
    patient_id = claims.get("patient_id")
    subject = claims.get("sub")
    actor_type = claims.get("actor_type")
    supabase_user_id = claims.get("supabase_user_id")
    auth_method = claims.get("auth_method")
    jti = claims.get("jti")
    issued_at = claims.get("iat")
    expires_at = claims.get("exp")

    if (
        not isinstance(patient_id, str)
        or not patient_id
        or subject != patient_id
        or actor_type != "patient"
        or not isinstance(supabase_user_id, str)
        or not supabase_user_id
        or auth_method != "phone_otp"
        or not isinstance(jti, str)
        or not jti
        or not isinstance(issued_at, int)
        or not isinstance(expires_at, int)
        or issued_at >= expires_at
    ):
        return None

    return PatientSession(
        patient_id=patient_id,
        supabase_user_id=supabase_user_id,
        auth_method=auth_method,
        jti_digest=_session_digest(jti),
        issued_at=issued_at,
        expires_at=expires_at,
    )


def session_binding_from_claims(claims: dict[str, Any]) -> str | None:
    """Return the stable opaque binding for one structurally valid JWT session."""
    record = _record_from_claims(claims)
    return record.session_binding if record is not None else None


async def activate_patient_session(claims: dict[str, Any]) -> PatientSession:
    """Create the live authority record for a newly issued patient JWT.

    The session record and per-patient revocation index are written together.
    Redis loss fails closed: callers must not return the JWT as usable authority
    if this activation cannot complete.
    """

    record = _record_from_claims(claims)
    if record is None:
        raise ValueError("Invalid patient session claims")

    now = int(time.time())
    ttl = record.expires_at - now
    if ttl <= 0:
        raise ValueError("Patient session is already expired")

    payload = json.dumps(
        {
            "patient_id": record.patient_id,
            "supabase_user_id": record.supabase_user_id,
            "auth_method": record.auth_method,
            "jti_digest": record.jti_digest,
            "issued_at": record.issued_at,
            "expires_at": record.expires_at,
            "status": "active",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    redis = get_async_redis_client()
    session_key = _session_key(record.jti_digest)
    index_key = _index_key(record.patient_id)

    try:
        if hasattr(redis, "eval"):
            script = """
            redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
            redis.call('SADD', KEYS[2], ARGV[3])
            local current_ttl = redis.call('TTL', KEYS[2])
            if current_ttl < tonumber(ARGV[2]) then
                redis.call('EXPIRE', KEYS[2], ARGV[2])
            end
            return 1
            """
            result = await _maybe_await(
                redis.eval(
                    script,
                    2,
                    session_key,
                    index_key,
                    payload,
                    ttl,
                    record.jti_digest,
                )
            )
            if int(result) != 1:
                raise RuntimeError("patient session activation rejected")
        else:
            await _maybe_await(redis.setex(session_key, ttl, payload))
            await _maybe_await(redis.sadd(index_key, record.jti_digest))
            await _maybe_await(redis.expire(index_key, ttl))
    except Exception as exc:
        raise PatientSessionStoreUnavailable(
            "Patient session store is unavailable"
        ) from exc

    return record


async def validate_patient_session(claims: dict[str, Any]) -> PatientSession | None:
    """Validate current server-side authority for decoded patient JWT claims."""

    record = _record_from_claims(claims)
    if record is None or record.expires_at <= int(time.time()):
        return None

    try:
        raw = await _maybe_await(
            get_async_redis_client().get(_session_key(record.jti_digest))
        )
    except Exception as exc:
        raise PatientSessionStoreUnavailable(
            "Patient session store is unavailable"
        ) from exc

    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str):
        return None

    try:
        stored = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(stored, dict) or stored.get("status") != "active":
        return None

    string_fields = {
        "patient_id": record.patient_id,
        "supabase_user_id": record.supabase_user_id,
        "auth_method": record.auth_method,
        "jti_digest": record.jti_digest,
    }
    for key, expected in string_fields.items():
        actual = stored.get(key)
        if not isinstance(actual, str) or not secrets.compare_digest(actual, expected):
            return None

    if (
        stored.get("issued_at") != record.issued_at
        or stored.get("expires_at") != record.expires_at
    ):
        return None

    return record


async def revoke_patient_session(claims: dict[str, Any]) -> bool:
    """Revoke exactly one patient session immediately."""

    record = _record_from_claims(claims)
    if record is None:
        return False

    redis = get_async_redis_client()
    session_key = _session_key(record.jti_digest)
    index_key = _index_key(record.patient_id)
    try:
        if hasattr(redis, "eval"):
            script = """
            local existed = redis.call('DEL', KEYS[1])
            redis.call('SREM', KEYS[2], ARGV[1])
            return existed
            """
            result = await _maybe_await(
                redis.eval(script, 2, session_key, index_key, record.jti_digest)
            )
            return bool(int(result))
        existed = await _maybe_await(redis.delete(session_key))
        await _maybe_await(redis.srem(index_key, record.jti_digest))
        return bool(existed)
    except Exception as exc:
        raise PatientSessionStoreUnavailable(
            "Patient session store is unavailable"
        ) from exc


async def revoke_all_patient_sessions(patient_id: str) -> int:
    """Revoke every currently indexed session for one patient."""

    if not patient_id:
        return 0
    redis = get_async_redis_client()
    index_key = _index_key(patient_id)
    try:
        if hasattr(redis, "eval"):
            script = """
            local members = redis.call('SMEMBERS', KEYS[1])
            local count = 0
            for _, digest in ipairs(members) do
                count = count + redis.call('DEL', ARGV[1] .. digest)
            end
            redis.call('DEL', KEYS[1])
            return count
            """
            result = await _maybe_await(
                redis.eval(script, 1, index_key, PATIENT_SESSION_PREFIX)
            )
            return int(result)

        members = await _maybe_await(redis.smembers(index_key))
        count = 0
        for member in members or set():
            if isinstance(member, bytes):
                member = member.decode("utf-8")
            if isinstance(member, str):
                count += int(
                    bool(await _maybe_await(redis.delete(_session_key(member))))
                )
        await _maybe_await(redis.delete(index_key))
        return count
    except Exception as exc:
        raise PatientSessionStoreUnavailable(
            "Patient session store is unavailable"
        ) from exc
