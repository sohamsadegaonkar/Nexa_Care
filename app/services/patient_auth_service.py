"""Patient phone-OTP tokens and first-device enrollment grants."""

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
    patient_id: str, supabase_user_id: str
) -> tuple[str, datetime]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=PATIENT_ACCESS_TTL_SECONDS)
    claims = {
        "sub": patient_id,
        "actor_type": "patient",
        "patient_id": patient_id,
        "supabase_user_id": supabase_user_id,
        "auth_method": "phone_otp",
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": secrets.token_urlsafe(24),
    }
    return jwt.encode(claims, _jwt_secret(), algorithm="HS256"), expires


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


def _token_key(token: str) -> str:
    return _ENROLL_PREFIX + hashlib.sha256(token.encode()).hexdigest()


async def _maybe_await(value):
    return await value if hasattr(value, "__await__") else value


async def issue_device_enrollment_token(patient_id: str, auth_session_id: str) -> str:
    token = secrets.token_urlsafe(32)
    payload = json.dumps(
        {
            "patient_id": patient_id,
            "auth_session_id": auth_session_id,
            "scope": "device_enrollment",
        }
    )
    await _maybe_await(
        get_redis_client().setex(
            _token_key(token), DEVICE_ENROLLMENT_TTL_SECONDS, payload
        )
    )
    return token


async def claim_device_enrollment_token(token: str, patient_id: str) -> str | None:
    redis = get_redis_client()
    key = _token_key(token)
    raw = await _maybe_await(redis.get(key))
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
    ):
        return None
    claim_id = secrets.token_urlsafe(24)
    claimed = await _maybe_await(
        redis.set(_CLAIM_PREFIX + key, claim_id, nx=True, ex=60)
    )
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
        consumed = await _maybe_await(redis.eval(script, 2, key, claim_key, claim_id))
        return bool(consumed)
    current = await _maybe_await(redis.get(claim_key))
    if isinstance(current, bytes):
        current = current.decode()
    if current != claim_id:
        return False
    await _maybe_await(redis.delete(key, claim_key))
    return True


async def release_device_enrollment_claim(token: str, claim_id: str) -> None:
    redis = get_redis_client()
    claim_key = _CLAIM_PREFIX + _token_key(token)
    current = await _maybe_await(redis.get(claim_key))
    if isinstance(current, bytes):
        current = current.decode()
    if current == claim_id:
        await _maybe_await(redis.delete(claim_key))
