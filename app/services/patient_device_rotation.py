"""One-time patient device-key rotation authority.

Slice 6D binds a short-lived Redis challenge to the exact current patient
session, logical device, current key version, operation, and new public-key
fingerprint. The current private key signs a canonical versioned payload; the
backend never receives or reconstructs a patient private key.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from app.core.redis import get_async_redis_client as get_redis_client
from app.services.patient_session_authority import (
    PatientSessionAuthorityUnavailable,
    resolve_patient_session_id,
)

DEVICE_ROTATION_PROTOCOL_VERSION = "nexa-device-key-rotation-v1"
DEVICE_ROTATION_OPERATION = "rotate_device_key"
DEVICE_ROTATION_CHALLENGE_TTL_SECONDS = 120
_ROTATION_PREFIX = "nexa:device_rotation:"


class DeviceRotationAuthorityUnavailable(RuntimeError):
    """Raised when Redis/session authority cannot be proven safely."""


class DeviceRotationChallengeError(ValueError):
    """Deterministic non-secret rotation challenge denial."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class DeviceRotationChallenge:
    nonce: str
    patient_id: str
    session_id: str
    device_id: str
    current_key_version: int
    new_public_key_fingerprint: str
    issued_at: str
    expires_at: str
    signing_payload: bytes


def _session_binding(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _challenge_key(nonce: str) -> str:
    digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    return f"{_ROTATION_PREFIX}{digest}"


async def _maybe_await(value):
    return await value if hasattr(value, "__await__") else value


def canonical_device_rotation_payload(
    *,
    patient_id: str,
    session_id: str,
    device_id: str,
    current_key_version: int,
    new_public_key_fingerprint: str,
    challenge_nonce: str,
    issued_at: str,
    expires_at: str,
) -> bytes:
    """Serialize every security-relevant rotation field as canonical JSON."""

    payload = {
        "challenge_nonce": challenge_nonce,
        "current_key_version": current_key_version,
        "device_id": device_id,
        "expires_at": expires_at,
        "issued_at": issued_at,
        "new_public_key_fingerprint": new_public_key_fingerprint,
        "operation": DEVICE_ROTATION_OPERATION,
        "patient_id": patient_id,
        "protocol_version": DEVICE_ROTATION_PROTOCOL_VERSION,
        "session_binding_sha256": _session_binding(session_id),
    }
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _record_for_challenge(challenge: DeviceRotationChallenge) -> str:
    return json.dumps(
        {
            "nonce": challenge.nonce,
            "patient_id": challenge.patient_id,
            "session_id": challenge.session_id,
            "device_id": challenge.device_id,
            "current_key_version": challenge.current_key_version,
            "new_public_key_fingerprint": challenge.new_public_key_fingerprint,
            "operation": DEVICE_ROTATION_OPERATION,
            "protocol_version": DEVICE_ROTATION_PROTOCOL_VERSION,
            "issued_at": challenge.issued_at,
            "expires_at": challenge.expires_at,
            "signing_payload_sha256": hashlib.sha256(
                challenge.signing_payload
            ).hexdigest(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _decode_record(raw: object) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


async def _require_live_session(patient_id: str, session_id: str) -> None:
    try:
        current = await resolve_patient_session_id(
            patient_id=patient_id, session_id=session_id
        )
    except PatientSessionAuthorityUnavailable as exc:
        raise DeviceRotationAuthorityUnavailable(
            "Device rotation session authority is unavailable"
        ) from exc
    if current is None:
        raise DeviceRotationChallengeError("DEVICE_ROTATION_SESSION_INACTIVE")


async def issue_device_rotation_challenge(
    *,
    patient_id: str,
    session_id: str,
    device_id: str,
    current_key_version: int,
    new_public_key_fingerprint: str,
) -> DeviceRotationChallenge:
    """Issue one short-lived challenge bound to exact live patient authority."""

    if current_key_version < 1:
        raise DeviceRotationChallengeError("DEVICE_KEY_VERSION_STALE")
    await _require_live_session(patient_id, session_id)

    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=DEVICE_ROTATION_CHALLENGE_TTL_SECONDS)
    issued_at = now.isoformat()
    expires_at = expires.isoformat()

    redis = get_redis_client()
    for _ in range(3):
        nonce = secrets.token_urlsafe(32)
        signing_payload = canonical_device_rotation_payload(
            patient_id=patient_id,
            session_id=session_id,
            device_id=device_id,
            current_key_version=current_key_version,
            new_public_key_fingerprint=new_public_key_fingerprint,
            challenge_nonce=nonce,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        challenge = DeviceRotationChallenge(
            nonce=nonce,
            patient_id=patient_id,
            session_id=session_id,
            device_id=device_id,
            current_key_version=current_key_version,
            new_public_key_fingerprint=new_public_key_fingerprint,
            issued_at=issued_at,
            expires_at=expires_at,
            signing_payload=signing_payload,
        )
        try:
            stored = await _maybe_await(
                redis.set(
                    _challenge_key(nonce),
                    _record_for_challenge(challenge),
                    nx=True,
                    ex=DEVICE_ROTATION_CHALLENGE_TTL_SECONDS,
                )
            )
        except Exception as exc:
            raise DeviceRotationAuthorityUnavailable(
                "Device rotation authority store is unavailable"
            ) from exc
        if stored:
            return challenge

    raise DeviceRotationAuthorityUnavailable(
        "Device rotation challenge could not be allocated"
    )


async def consume_device_rotation_challenge(
    *,
    challenge_nonce: str,
    patient_id: str,
    session_id: str,
    device_id: str,
    current_key_version: int,
    new_public_key_fingerprint: str,
) -> DeviceRotationChallenge:
    """Atomically consume one challenge and verify every stored binding."""

    await _require_live_session(patient_id, session_id)
    redis = get_redis_client()
    key = _challenge_key(challenge_nonce)

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
        raise DeviceRotationAuthorityUnavailable(
            "Device rotation authority store is unavailable"
        ) from exc

    record = _decode_record(raw)
    if record is None:
        raise DeviceRotationChallengeError("DEVICE_ROTATION_CHALLENGE_INVALID")

    expected = {
        "nonce": challenge_nonce,
        "patient_id": patient_id,
        "session_id": session_id,
        "device_id": device_id,
        "current_key_version": current_key_version,
        "new_public_key_fingerprint": new_public_key_fingerprint,
        "operation": DEVICE_ROTATION_OPERATION,
        "protocol_version": DEVICE_ROTATION_PROTOCOL_VERSION,
    }
    for field, value in expected.items():
        if record.get(field) != value:
            raise DeviceRotationChallengeError("DEVICE_ROTATION_BINDING_MISMATCH")

    issued_at = record.get("issued_at")
    expires_at = record.get("expires_at")
    if not isinstance(issued_at, str) or not isinstance(expires_at, str):
        raise DeviceRotationChallengeError("DEVICE_ROTATION_CHALLENGE_INVALID")
    try:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeviceRotationChallengeError(
            "DEVICE_ROTATION_CHALLENGE_INVALID"
        ) from exc
    if expiry.tzinfo is None or datetime.now(timezone.utc) > expiry:
        raise DeviceRotationChallengeError("DEVICE_ROTATION_CHALLENGE_EXPIRED")

    signing_payload = canonical_device_rotation_payload(
        patient_id=patient_id,
        session_id=session_id,
        device_id=device_id,
        current_key_version=current_key_version,
        new_public_key_fingerprint=new_public_key_fingerprint,
        challenge_nonce=challenge_nonce,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    if record.get("signing_payload_sha256") != hashlib.sha256(
        signing_payload
    ).hexdigest():
        raise DeviceRotationChallengeError("DEVICE_ROTATION_CHALLENGE_INVALID")

    return DeviceRotationChallenge(
        nonce=challenge_nonce,
        patient_id=patient_id,
        session_id=session_id,
        device_id=device_id,
        current_key_version=current_key_version,
        new_public_key_fingerprint=new_public_key_fingerprint,
        issued_at=issued_at,
        expires_at=expires_at,
        signing_payload=signing_payload,
    )


def verify_device_rotation_signature(
    *, public_key_der: bytes, signing_payload: bytes, signature_b64: str
) -> bool:
    """Verify a rotation proof using the currently active P-256 public key."""

    try:
        signature = base64.b64decode(signature_b64, validate=True)
        public_key = serialization.load_der_public_key(public_key_der)
        if not (
            isinstance(public_key, ec.EllipticCurvePublicKey)
            and isinstance(public_key.curve, ec.SECP256R1)
        ):
            return False
        public_key.verify(
            signature,
            hashlib.sha256(signing_payload).digest(),
            ec.ECDSA(utils.Prehashed(hashes.SHA256())),
        )
    except Exception:
        return False
    return True
