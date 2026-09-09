"""Lost-device recovery and trusted-device enrollment authority.

Slice 6E keeps account authentication distinct from cryptographic device authority.
Recovery requires a second fresh Supabase OTP and a short-lived one-time Redis
capability bound to the exact patient, upstream identity subject, current Nexa
session, and recovery operation. Trusted-device enrollment uses proof from an
already-active device key over a separate versioned one-time challenge.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.redis import get_async_redis_client as get_redis_client
from app.services.patient_session_authority import (
    PatientSessionAuthorityUnavailable,
    resolve_patient_session_id,
)

PATIENT_RECOVERY_OPERATION = "recover_patient_device_authority"
PATIENT_RECOVERY_TTL_SECONDS = 5 * 60
_RECOVERY_PREFIX = "nexa:patient_recovery:"
_RECOVERY_SLOT_PREFIX = "nexa:patient_recovery_slot:"

TRUSTED_ENROLLMENT_PROTOCOL_VERSION = "nexa-trusted-device-enrollment-v1"
TRUSTED_ENROLLMENT_OPERATION = "authorize_device_enrollment"
TRUSTED_ENROLLMENT_CHALLENGE_TTL_SECONDS = 120
_TRUSTED_ENROLLMENT_PREFIX = "nexa:trusted_device_enrollment:"


class PatientRecoveryAuthorityUnavailable(RuntimeError):
    """Raised when Redis/session authority cannot be proven safely."""


class PatientRecoveryCapabilityError(ValueError):
    """Deterministic non-secret recovery-capability denial."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TrustedEnrollmentAuthorityUnavailable(RuntimeError):
    """Raised when Redis/session authority cannot be proven safely."""


class TrustedEnrollmentChallengeError(ValueError):
    """Deterministic non-secret trusted-enrollment denial."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class PatientRecoveryCapability:
    token: str
    patient_id: str
    session_id: str
    supabase_user_id: str
    issued_at: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class TrustedEnrollmentChallenge:
    nonce: str
    patient_id: str
    session_id: str
    authorizer_device_id: str
    authorizer_key_version: int
    new_public_key_fingerprint: str
    issued_at: str
    expires_at: str
    signing_payload: bytes


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _recovery_key(token: str) -> str:
    return f"{_RECOVERY_PREFIX}{_sha256(token)}"


def _recovery_slot_key(patient_id: str, session_id: str) -> str:
    return f"{_RECOVERY_SLOT_PREFIX}{_sha256(patient_id + ':' + session_id)}"


def _trusted_challenge_key(nonce: str) -> str:
    return f"{_TRUSTED_ENROLLMENT_PREFIX}{_sha256(nonce)}"


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


async def _require_live_session(
    *,
    patient_id: str,
    session_id: str,
    supabase_user_id: str | None,
    error_cls,
    inactive_code: str,
) -> dict[str, Any]:
    try:
        current = await resolve_patient_session_id(
            patient_id=patient_id, session_id=session_id
        )
    except PatientSessionAuthorityUnavailable as exc:
        raise error_cls("Patient session authority is unavailable") from exc
    if current is None:
        raise (
            PatientRecoveryCapabilityError(inactive_code)
            if error_cls is PatientRecoveryAuthorityUnavailable
            else TrustedEnrollmentChallengeError(inactive_code)
        )
    if supabase_user_id is not None and current.get("supabase_user_id") != supabase_user_id:
        raise PatientRecoveryCapabilityError("PATIENT_RECOVERY_IDENTITY_MISMATCH")
    return current


async def issue_patient_recovery_capability(
    *,
    patient_id: str,
    session_id: str,
    supabase_user_id: str,
) -> PatientRecoveryCapability:
    """Issue at most one recovery capability for one exact live patient session."""

    await _require_live_session(
        patient_id=patient_id,
        session_id=session_id,
        supabase_user_id=supabase_user_id,
        error_cls=PatientRecoveryAuthorityUnavailable,
        inactive_code="PATIENT_RECOVERY_SESSION_INACTIVE",
    )

    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=PATIENT_RECOVERY_TTL_SECONDS)
    token = secrets.token_urlsafe(32)
    token_digest = _sha256(token)
    payload = json.dumps(
        {
            "scope": "patient_device_recovery",
            "operation": PATIENT_RECOVERY_OPERATION,
            "patient_id": patient_id,
            "auth_session_id": session_id,
            "supabase_user_id": supabase_user_id,
            "issued_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "token_digest": token_digest,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    key = _recovery_key(token)
    slot_key = _recovery_slot_key(patient_id, session_id)
    redis = get_redis_client()
    script = """
    if redis.call('EXISTS', KEYS[2]) == 1 then
        return 0
    end
    redis.call('SETEX', KEYS[1], ARGV[1], ARGV[2])
    redis.call('SETEX', KEYS[2], ARGV[1], ARGV[3])
    return 1
    """
    try:
        if hasattr(redis, "eval"):
            stored = await _maybe_await(
                redis.eval(
                    script,
                    2,
                    key,
                    slot_key,
                    PATIENT_RECOVERY_TTL_SECONDS,
                    payload,
                    token_digest,
                )
            )
        else:
            existing = await _maybe_await(redis.get(slot_key))
            if existing:
                stored = 0
            else:
                await _maybe_await(
                    redis.setex(key, PATIENT_RECOVERY_TTL_SECONDS, payload)
                )
                await _maybe_await(
                    redis.setex(slot_key, PATIENT_RECOVERY_TTL_SECONDS, token_digest)
                )
                stored = 1
    except Exception as exc:
        raise PatientRecoveryAuthorityUnavailable(
            "Patient recovery authority store is unavailable"
        ) from exc
    if not stored:
        raise PatientRecoveryCapabilityError("PATIENT_RECOVERY_CAPABILITY_ALREADY_ISSUED")
    return PatientRecoveryCapability(
        token=token,
        patient_id=patient_id,
        session_id=session_id,
        supabase_user_id=supabase_user_id,
        issued_at=now.isoformat(),
        expires_at=expires.isoformat(),
    )


async def consume_patient_recovery_capability(
    *,
    token: str,
    patient_id: str,
    session_id: str,
    supabase_user_id: str,
) -> PatientRecoveryCapability:
    """Atomically consume a recovery capability after exact live-session validation."""

    await _require_live_session(
        patient_id=patient_id,
        session_id=session_id,
        supabase_user_id=supabase_user_id,
        error_cls=PatientRecoveryAuthorityUnavailable,
        inactive_code="PATIENT_RECOVERY_SESSION_INACTIVE",
    )
    redis = get_redis_client()
    key = _recovery_key(token)
    slot_key = _recovery_slot_key(patient_id, session_id)
    token_digest = _sha256(token)
    script = """
    local raw = redis.call('GET', KEYS[1])
    if not raw then return false end
    local ok, payload = pcall(cjson.decode, raw)
    if not ok then return false end
    if payload['scope'] ~= ARGV[1]
       or payload['operation'] ~= ARGV[2]
       or payload['patient_id'] ~= ARGV[3]
       or payload['auth_session_id'] ~= ARGV[4]
       or payload['supabase_user_id'] ~= ARGV[5]
       or payload['token_digest'] ~= ARGV[6] then
        return false
    end
    if redis.call('GET', KEYS[2]) ~= ARGV[6] then
        return false
    end
    redis.call('DEL', KEYS[1], KEYS[2])
    return raw
    """
    try:
        if hasattr(redis, "eval"):
            raw = await _maybe_await(
                redis.eval(
                    script,
                    2,
                    key,
                    slot_key,
                    "patient_device_recovery",
                    PATIENT_RECOVERY_OPERATION,
                    patient_id,
                    session_id,
                    supabase_user_id,
                    token_digest,
                )
            )
        else:
            raw = await _maybe_await(redis.get(key))
            record = _decode_json_object(raw)
            slot = await _maybe_await(redis.get(slot_key))
            if isinstance(slot, bytes):
                slot = slot.decode("utf-8")
            if (
                record is None
                or record.get("scope") != "patient_device_recovery"
                or record.get("operation") != PATIENT_RECOVERY_OPERATION
                or record.get("patient_id") != patient_id
                or record.get("auth_session_id") != session_id
                or record.get("supabase_user_id") != supabase_user_id
                or record.get("token_digest") != token_digest
                or slot != token_digest
            ):
                raw = None
            else:
                await _maybe_await(redis.delete(key, slot_key))
    except Exception as exc:
        raise PatientRecoveryAuthorityUnavailable(
            "Patient recovery authority store is unavailable"
        ) from exc

    record = _decode_json_object(raw)
    if record is None:
        raise PatientRecoveryCapabilityError("PATIENT_RECOVERY_CAPABILITY_INVALID")
    issued_at = record.get("issued_at")
    expires_at = record.get("expires_at")
    if not isinstance(issued_at, str) or not isinstance(expires_at, str):
        raise PatientRecoveryCapabilityError("PATIENT_RECOVERY_CAPABILITY_INVALID")
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PatientRecoveryCapabilityError(
            "PATIENT_RECOVERY_CAPABILITY_INVALID"
        ) from exc
    if expiry.tzinfo is None or datetime.now(timezone.utc) > expiry:
        raise PatientRecoveryCapabilityError("PATIENT_RECOVERY_CAPABILITY_EXPIRED")
    return PatientRecoveryCapability(
        token=token,
        patient_id=patient_id,
        session_id=session_id,
        supabase_user_id=supabase_user_id,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def canonical_trusted_enrollment_payload(
    *,
    patient_id: str,
    session_id: str,
    authorizer_device_id: str,
    authorizer_key_version: int,
    new_public_key_fingerprint: str,
    challenge_nonce: str,
    issued_at: str,
    expires_at: str,
) -> bytes:
    payload = {
        "authorizer_device_id": authorizer_device_id,
        "authorizer_key_version": authorizer_key_version,
        "challenge_nonce": challenge_nonce,
        "expires_at": expires_at,
        "issued_at": issued_at,
        "new_public_key_fingerprint": new_public_key_fingerprint,
        "operation": TRUSTED_ENROLLMENT_OPERATION,
        "patient_id": patient_id,
        "protocol_version": TRUSTED_ENROLLMENT_PROTOCOL_VERSION,
        "session_binding_sha256": _sha256(session_id),
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


async def issue_trusted_enrollment_challenge(
    *,
    patient_id: str,
    session_id: str,
    authorizer_device_id: str,
    authorizer_key_version: int,
    new_public_key_fingerprint: str,
) -> TrustedEnrollmentChallenge:
    if authorizer_key_version < 1:
        raise TrustedEnrollmentChallengeError("DEVICE_KEY_VERSION_STALE")
    await _require_live_session(
        patient_id=patient_id,
        session_id=session_id,
        supabase_user_id=None,
        error_cls=TrustedEnrollmentAuthorityUnavailable,
        inactive_code="TRUSTED_ENROLLMENT_SESSION_INACTIVE",
    )
    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=TRUSTED_ENROLLMENT_CHALLENGE_TTL_SECONDS)
    issued_at = now.isoformat()
    expires_at = expires.isoformat()
    redis = get_redis_client()
    for _ in range(3):
        nonce = secrets.token_urlsafe(32)
        signing_payload = canonical_trusted_enrollment_payload(
            patient_id=patient_id,
            session_id=session_id,
            authorizer_device_id=authorizer_device_id,
            authorizer_key_version=authorizer_key_version,
            new_public_key_fingerprint=new_public_key_fingerprint,
            challenge_nonce=nonce,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        record = json.dumps(
            {
                "nonce": nonce,
                "patient_id": patient_id,
                "session_id": session_id,
                "authorizer_device_id": authorizer_device_id,
                "authorizer_key_version": authorizer_key_version,
                "new_public_key_fingerprint": new_public_key_fingerprint,
                "operation": TRUSTED_ENROLLMENT_OPERATION,
                "protocol_version": TRUSTED_ENROLLMENT_PROTOCOL_VERSION,
                "issued_at": issued_at,
                "expires_at": expires_at,
                "signing_payload_sha256": hashlib.sha256(signing_payload).hexdigest(),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            stored = await _maybe_await(
                redis.set(
                    _trusted_challenge_key(nonce),
                    record,
                    nx=True,
                    ex=TRUSTED_ENROLLMENT_CHALLENGE_TTL_SECONDS,
                )
            )
        except Exception as exc:
            raise TrustedEnrollmentAuthorityUnavailable(
                "Trusted enrollment authority store is unavailable"
            ) from exc
        if stored:
            return TrustedEnrollmentChallenge(
                nonce=nonce,
                patient_id=patient_id,
                session_id=session_id,
                authorizer_device_id=authorizer_device_id,
                authorizer_key_version=authorizer_key_version,
                new_public_key_fingerprint=new_public_key_fingerprint,
                issued_at=issued_at,
                expires_at=expires_at,
                signing_payload=signing_payload,
            )
    raise TrustedEnrollmentAuthorityUnavailable(
        "Trusted enrollment challenge could not be allocated"
    )


async def consume_trusted_enrollment_challenge(
    *,
    challenge_nonce: str,
    patient_id: str,
    session_id: str,
    authorizer_device_id: str,
    authorizer_key_version: int,
    new_public_key_fingerprint: str,
) -> TrustedEnrollmentChallenge:
    await _require_live_session(
        patient_id=patient_id,
        session_id=session_id,
        supabase_user_id=None,
        error_cls=TrustedEnrollmentAuthorityUnavailable,
        inactive_code="TRUSTED_ENROLLMENT_SESSION_INACTIVE",
    )
    redis = get_redis_client()
    key = _trusted_challenge_key(challenge_nonce)
    try:
        if hasattr(redis, "eval"):
            script = """
            local raw = redis.call('GET', KEYS[1])
            if not raw then return false end
            redis.call('DEL', KEYS[1])
            return raw
            """
            raw = await _maybe_await(redis.eval(script, 1, key))
        else:
            raw = await _maybe_await(redis.get(key))
            if raw:
                await _maybe_await(redis.delete(key))
    except Exception as exc:
        raise TrustedEnrollmentAuthorityUnavailable(
            "Trusted enrollment authority store is unavailable"
        ) from exc

    record = _decode_json_object(raw)
    if record is None:
        raise TrustedEnrollmentChallengeError("TRUSTED_ENROLLMENT_CHALLENGE_INVALID")
    expected = {
        "nonce": challenge_nonce,
        "patient_id": patient_id,
        "session_id": session_id,
        "authorizer_device_id": authorizer_device_id,
        "authorizer_key_version": authorizer_key_version,
        "new_public_key_fingerprint": new_public_key_fingerprint,
        "operation": TRUSTED_ENROLLMENT_OPERATION,
        "protocol_version": TRUSTED_ENROLLMENT_PROTOCOL_VERSION,
    }
    for field, value in expected.items():
        if record.get(field) != value:
            raise TrustedEnrollmentChallengeError("TRUSTED_ENROLLMENT_BINDING_MISMATCH")
    issued_at = record.get("issued_at")
    expires_at = record.get("expires_at")
    if not isinstance(issued_at, str) or not isinstance(expires_at, str):
        raise TrustedEnrollmentChallengeError("TRUSTED_ENROLLMENT_CHALLENGE_INVALID")
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TrustedEnrollmentChallengeError(
            "TRUSTED_ENROLLMENT_CHALLENGE_INVALID"
        ) from exc
    if expiry.tzinfo is None or datetime.now(timezone.utc) > expiry:
        raise TrustedEnrollmentChallengeError("TRUSTED_ENROLLMENT_CHALLENGE_EXPIRED")
    signing_payload = canonical_trusted_enrollment_payload(
        patient_id=patient_id,
        session_id=session_id,
        authorizer_device_id=authorizer_device_id,
        authorizer_key_version=authorizer_key_version,
        new_public_key_fingerprint=new_public_key_fingerprint,
        challenge_nonce=challenge_nonce,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    if record.get("signing_payload_sha256") != hashlib.sha256(
        signing_payload
    ).hexdigest():
        raise TrustedEnrollmentChallengeError("TRUSTED_ENROLLMENT_CHALLENGE_INVALID")
    return TrustedEnrollmentChallenge(
        nonce=challenge_nonce,
        patient_id=patient_id,
        session_id=session_id,
        authorizer_device_id=authorizer_device_id,
        authorizer_key_version=authorizer_key_version,
        new_public_key_fingerprint=new_public_key_fingerprint,
        issued_at=issued_at,
        expires_at=expires_at,
        signing_payload=signing_payload,
    )
