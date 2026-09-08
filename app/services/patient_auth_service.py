"""Patient phone-OTP tokens, authoritative sessions, and device-enrollment grants."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt

from app.core.redis import get_async_redis_client as get_redis_client
from app.services.patient_session_authority import (
    PatientSessionAuthorityUnavailable,
    create_patient_session,
    get_or_create_patient_session_epoch,
    resolve_patient_session_id,
)

PATIENT_ACCESS_TTL_SECONDS = 15 * 60
DEVICE_ENROLLMENT_TTL_SECONDS = 5 * 60
_ENROLL_PREFIX = "nexa:device_enrollment:"
_CLAIM_PREFIX = "nexa:device_enrollment_claim:"


def normalize_indian_phone(value: str) -> str:
    digits = re.sub(r"[\s()-]", "", value.strip())
    if digits.startswith("+91"):
        national = digits[3:]
    elif digits.startswith("91") and len(digits) == 12:
        national = digits[2:]
    else:
        national = digits
    if not re.fullmatch(r"[6-9]\d{9}", national):
        raise ValueError("Enter a valid Indian mobile number.")
    return f"+91{national}"


def _jwt_secret() -> str:
    value = os.getenv("PATIENT_JWT_SECRET", "").strip()
    if len(value) < 32:
        raise RuntimeError(
            "PATIENT_JWT_SECRET must be configured with at least 32 characters"
        )
    return value


def issue_patient_access_token(
    patient_id: str,
    supabase_user_id: str,
    *,
    session_id: str | None = None,
    session_epoch: int | None = None,
) -> tuple[str, datetime]:
    """Encode a patient JWT; live authority is established separately in Redis.

    ``session_id`` and ``session_epoch`` may be omitted only for low-level JWT
    tests and compatibility helpers. Runtime patient login uses
    :func:`issue_patient_access_session`, which always includes both claims and
    creates the matching live server-side session before returning the token.
    """

    if (session_id is None) != (session_epoch is None):
        raise ValueError("session_id and session_epoch must be supplied together")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=PATIENT_ACCESS_TTL_SECONDS)
    claims: dict[str, Any] = {
        "sub": patient_id,
        "actor_type": "patient",
        "patient_id": patient_id,
        "supabase_user_id": supabase_user_id,
        "auth_method": "phone_otp",
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": secrets.token_urlsafe(24),
    }
    if session_id is not None and session_epoch is not None:
        claims["sid"] = session_id
        claims["session_epoch"] = session_epoch
    return jwt.encode(claims, _jwt_secret(), algorithm="HS256"), expires


async def issue_patient_access_session(
    patient_id: str, supabase_user_id: str
) -> tuple[str, datetime, str]:
    """Issue a patient JWT only after establishing matching live authority."""

    session_id = secrets.token_urlsafe(32)
    session_epoch = await get_or_create_patient_session_epoch(patient_id)
    issued_at = datetime.now(timezone.utc)
    access_token, expires_at = issue_patient_access_token(
        patient_id,
        supabase_user_id,
        session_id=session_id,
        session_epoch=session_epoch,
    )
    await create_patient_session(
        patient_id=patient_id,
        supabase_user_id=supabase_user_id,
        session_id=session_id,
        session_epoch=session_epoch,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    return access_token, expires_at, session_id


def decode_patient_access_token(token: str) -> dict[str, Any] | None:
    clean = token.removeprefix("Bearer ").strip()
    try:
        claims = jwt.decode(clean, _jwt_secret(), algorithms=["HS256"])
    except (jwt.PyJWTError, RuntimeError):
        return None
    if (
        claims.get("actor_type") != "patient"
        or claims.get("auth_method") != "phone_otp"
    ):
        return None
    if claims.get("sub") != claims.get("patient_id"):
        return None
    return claims


def patient_session_id_from_token(token: str | None) -> str | None:
    """Return the opaque Nexa patient session identifier from a valid JWT."""

    if not token:
        return None
    claims = decode_patient_access_token(token)
    if not claims:
        return None
    session_id = claims.get("sid")
    return session_id if isinstance(session_id, str) and len(session_id) >= 16 else None


def _token_key(token: str) -> str:
    return _ENROLL_PREFIX + hashlib.sha256(token.encode()).hexdigest()


async def _maybe_await(value):
    return await value if hasattr(value, "__await__") else value


async def issue_device_enrollment_token(patient_id: str, auth_session_id: str) -> str:
    """Issue an enrollment grant bound to one current patient session."""

    session = await resolve_patient_session_id(
        patient_id=patient_id, session_id=auth_session_id
    )
    if session is None:
        raise PatientSessionAuthorityUnavailable(
            "Cannot issue device enrollment authority for an inactive patient session"
        )

    token = secrets.token_urlsafe(32)
    payload = json.dumps(
        {
            "patient_id": patient_id,
            "auth_session_id": auth_session_id,
            "scope": "device_enrollment",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        await _maybe_await(
            get_redis_client().setex(
                _token_key(token), DEVICE_ENROLLMENT_TTL_SECONDS, payload
            )
        )
    except Exception as exc:
        raise PatientSessionAuthorityUnavailable(
            "Device enrollment authority store is unavailable"
        ) from exc
    return token


async def claim_device_enrollment_token(
    token: str, patient_id: str, auth_session_id: str | None = None
) -> str | None:
    """Reserve one enrollment grant only from the exact live issuing session."""

    if not auth_session_id:
        return None
    session = await resolve_patient_session_id(
        patient_id=patient_id, session_id=auth_session_id
    )
    if session is None:
        return None

    redis = get_redis_client()
    key = _token_key(token)
    try:
        raw = await _maybe_await(redis.get(key))
    except Exception as exc:
        raise PatientSessionAuthorityUnavailable(
            "Device enrollment authority store is unavailable"
        ) from exc
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if (
        payload.get("scope") != "device_enrollment"
        or payload.get("patient_id") != patient_id
        or payload.get("auth_session_id") != auth_session_id
    ):
        return None
    claim_id = secrets.token_urlsafe(24)
    try:
        claimed = await _maybe_await(
            redis.set(_CLAIM_PREFIX + key, claim_id, nx=True, ex=60)
        )
    except Exception as exc:
        raise PatientSessionAuthorityUnavailable(
            "Device enrollment authority store is unavailable"
        ) from exc
    return claim_id if claimed else None


async def finalize_device_enrollment_token(token: str, claim_id: str) -> bool:
    redis = get_redis_client()
    key = _token_key(token)
    claim_key = _CLAIM_PREFIX + key
    if hasattr(redis, "eval"):
        script = """
        if redis.call('GET', KEYS[2]) == ARGV[1] then
            redis.call('DEL', KEYS[1], KEYS[2])
            return 1
        end
        return 0
        """
        try:
            consumed = await _maybe_await(
                redis.eval(script, 2, key, claim_key, claim_id)
            )
        except Exception as exc:
            raise PatientSessionAuthorityUnavailable(
                "Device enrollment authority store is unavailable"
            ) from exc
        return bool(consumed)
    try:
        current = await _maybe_await(redis.get(claim_key))
    except Exception as exc:
        raise PatientSessionAuthorityUnavailable(
            "Device enrollment authority store is unavailable"
        ) from exc
    if isinstance(current, bytes):
        current = current.decode()
    if current != claim_id:
        return False
    try:
        await _maybe_await(redis.delete(key, claim_key))
    except Exception as exc:
        raise PatientSessionAuthorityUnavailable(
            "Device enrollment authority store is unavailable"
        ) from exc
    return True


async def release_device_enrollment_claim(token: str, claim_id: str) -> None:
    redis = get_redis_client()
    claim_key = _CLAIM_PREFIX + _token_key(token)
    try:
        current = await _maybe_await(redis.get(claim_key))
    except Exception as exc:
        raise PatientSessionAuthorityUnavailable(
            "Device enrollment authority store is unavailable"
        ) from exc
    if isinstance(current, bytes):
        current = current.decode()
    if current == claim_id:
        try:
            await _maybe_await(redis.delete(claim_key))
        except Exception as exc:
            raise PatientSessionAuthorityUnavailable(
                "Device enrollment authority store is unavailable"
            ) from exc
