"""One-time live/durable authority mint for approved Treatment Session V1 evidence.

This module creates authority material only after the patient has signed the
exact Treatment Session V1 context.  It deliberately does not expose a central
write gate; later Slice 10B work must still require the minted operation before
any encounter or clinical write can execute.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import get_async_redis_client
from app.models.clinical_access_session import ClinicalAccessSessionRecord
from app.security.clinical_access_policy import CLINICAL_ACCESS_POLICY_VERSION
from app.services.signed_treatment_session_v1 import (
    SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
    TreatmentSessionV1ProtocolError,
    normalize_treatment_operations,
)

TREATMENT_SESSION_V1_GRANT_TYPE: Final[str] = "treatment_session_v1"
TREATMENT_SESSION_V1_SCOPE: Final[str] = "treatment"
TREATMENT_CAPABILITY_PREFIX: Final[str] = "treatment_session_v1:capability:"
TREATMENT_CLAIM_PREFIX: Final[str] = "treatment_session_v1:claim:"

_CLAIM_ONCE_LUA = """
if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[3])
redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
return 1
"""


class TreatmentSessionV1MintError(RuntimeError):
    """Base error for fail-closed treatment-session minting."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class TreatmentSessionV1MintStoreUnavailable(TreatmentSessionV1MintError):
    """Redis authority store could not be safely mutated."""


class TreatmentSessionV1AlreadyClaimed(TreatmentSessionV1MintError):
    """The signed approval already has a live/replayed claim marker."""


@dataclass(frozen=True, slots=True)
class TreatmentSessionV1Capability:
    session_id: str
    patient_id: str
    provider_id: str
    hospital_id: str
    request_id: str
    purpose: str
    allowed_operations: tuple[str, ...]
    provider_session_binding_hash: str
    policy_version: str
    issued_at: str
    expires_at: str


def treatment_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def provider_session_binding_hash(raw_binding: str) -> str:
    if not isinstance(raw_binding, str) or not raw_binding.strip():
        raise TreatmentSessionV1MintError("TREATMENT_PROVIDER_SESSION_BINDING_REQUIRED")
    return hashlib.sha256(raw_binding.encode("utf-8")).hexdigest()


def provider_session_binding_matches(
    *, stored_hash: object, live_binding: str
) -> bool:
    if not isinstance(stored_hash, str) or len(stored_hash) != 64:
        return False
    try:
        candidate = provider_session_binding_hash(live_binding)
    except TreatmentSessionV1MintError:
        return False
    return secrets.compare_digest(stored_hash, candidate)


def _capability_key(digest: str) -> str:
    return f"{TREATMENT_CAPABILITY_PREFIX}{digest}"


def _claim_key(request_id: str) -> str:
    return f"{TREATMENT_CLAIM_PREFIX}{request_id}"


def _lower_sha256(value: object, *, code: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or value != value.lower():
        raise TreatmentSessionV1MintError(code)
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise TreatmentSessionV1MintError(code) from exc
    return value


def _aware(value: object, *, code: str) -> datetime:
    if not isinstance(value, str):
        raise TreatmentSessionV1MintError(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise TreatmentSessionV1MintError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TreatmentSessionV1MintError(code)
    return parsed


async def mint_treatment_session_v1_capability(
    *, request_data: dict, provider_session_binding: str
) -> tuple[str, TreatmentSessionV1Capability]:
    """Claim one approved V1 request once and store an opaque live capability."""

    if (
        request_data.get("protocol_version")
        != SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION
        or request_data.get("status") != "approved"
    ):
        raise TreatmentSessionV1MintError("TREATMENT_APPROVAL_INVALID")

    try:
        operations = normalize_treatment_operations(
            request_data.get("allowed_operations", [])
        )
    except TreatmentSessionV1ProtocolError as exc:
        raise TreatmentSessionV1MintError("TREATMENT_OPERATION_SET_INVALID") from exc

    stored_binding_hash = _lower_sha256(
        request_data.get("provider_session_binding_hash"),
        code="TREATMENT_PROVIDER_SESSION_BINDING_INVALID",
    )
    if not provider_session_binding_matches(
        stored_hash=stored_binding_hash, live_binding=provider_session_binding
    ):
        raise TreatmentSessionV1MintError("TREATMENT_PROVIDER_SESSION_MISMATCH")

    expires_at = _aware(
        request_data.get("approval_expires_at"),
        code="TREATMENT_ACCESS_WINDOW_INVALID",
    )
    now = datetime.now(timezone.utc)
    ttl_seconds = int((expires_at - now).total_seconds())
    if ttl_seconds <= 0:
        raise TreatmentSessionV1MintError("TREATMENT_ACCESS_EXPIRED")

    request_id = str(request_data.get("request_id", ""))
    patient_id = str(request_data.get("patient_id", ""))
    provider_id = str(request_data.get("provider_id", ""))
    hospital_id = str(request_data.get("hospital_id", ""))
    purpose = str(request_data.get("purpose", ""))
    if not request_id or not purpose:
        raise TreatmentSessionV1MintError("TREATMENT_APPROVAL_INVALID")
    try:
        uuid.UUID(request_id)
        uuid.UUID(patient_id)
        uuid.UUID(provider_id)
        uuid.UUID(hospital_id)
    except (TypeError, ValueError) as exc:
        raise TreatmentSessionV1MintError("TREATMENT_APPROVAL_INVALID") from exc

    token = secrets.token_urlsafe(48)
    digest = treatment_token_hash(token)
    session_id = str(uuid.uuid4())
    issued_at = now.isoformat()
    payload = {
        "protocol_version": SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
        "grant_type": TREATMENT_SESSION_V1_GRANT_TYPE,
        "session_id": session_id,
        "request_id": request_id,
        "patient_id": patient_id,
        "provider_id": provider_id,
        "hospital_id": hospital_id,
        "purpose": purpose,
        "scope": TREATMENT_SESSION_V1_SCOPE,
        "allowed_operations": list(operations),
        "provider_session_binding_hash": stored_binding_hash,
        "clinical_access_policy_version": CLINICAL_ACCESS_POLICY_VERSION,
        "issued_at": issued_at,
        "expires_at": expires_at.isoformat(),
    }

    try:
        redis = get_async_redis_client()
        claimed = await redis.eval(
            _CLAIM_ONCE_LUA,
            2,
            _claim_key(request_id),
            _capability_key(digest),
            json.dumps(payload, sort_keys=True),
            digest,
            ttl_seconds,
        )
        if int(claimed) != 1:
            raise TreatmentSessionV1AlreadyClaimed("TREATMENT_SESSION_ALREADY_CLAIMED")
    except TreatmentSessionV1AlreadyClaimed:
        raise
    except Exception as exc:
        raise TreatmentSessionV1MintStoreUnavailable(
            "TREATMENT_SESSION_SECURITY_STORE_UNAVAILABLE"
        ) from exc

    return token, TreatmentSessionV1Capability(
        session_id=session_id,
        patient_id=patient_id,
        provider_id=provider_id,
        hospital_id=hospital_id,
        request_id=request_id,
        purpose=purpose,
        allowed_operations=operations,
        provider_session_binding_hash=stored_binding_hash,
        policy_version=CLINICAL_ACCESS_POLICY_VERSION,
        issued_at=issued_at,
        expires_at=expires_at.isoformat(),
    )


async def invalidate_treatment_session_v1_request(request_id: str) -> None:
    """Delete any live token for a request so a failed durable mint cannot survive."""

    try:
        redis = get_async_redis_client()
        digest = await redis.get(_claim_key(request_id))
        if isinstance(digest, bytes):
            digest = digest.decode("utf-8")
        if isinstance(digest, str):
            await redis.delete(_capability_key(digest))
        await redis.delete(_claim_key(request_id))
    except Exception as exc:
        raise TreatmentSessionV1MintStoreUnavailable(
            "TREATMENT_SESSION_SECURITY_STORE_UNAVAILABLE"
        ) from exc


async def stage_treatment_session_v1(
    db: AsyncSession,
    *,
    capability: TreatmentSessionV1Capability,
    token_hash: str,
) -> ClinicalAccessSessionRecord:
    """Stage the durable treatment session in the caller's transaction."""

    try:
        normalized = normalize_treatment_operations(capability.allowed_operations)
    except TreatmentSessionV1ProtocolError as exc:
        raise TreatmentSessionV1MintError("TREATMENT_OPERATION_SET_INVALID") from exc
    if normalized != capability.allowed_operations:
        raise TreatmentSessionV1MintError("TREATMENT_OPERATION_SET_INVALID")
    if capability.policy_version != CLINICAL_ACCESS_POLICY_VERSION:
        raise TreatmentSessionV1MintError("TREATMENT_POLICY_VERSION_INVALID")

    issued_at = _aware(capability.issued_at, code="TREATMENT_SESSION_LIFETIME_INVALID")
    expires_at = _aware(capability.expires_at, code="TREATMENT_SESSION_LIFETIME_INVALID")
    if expires_at <= issued_at:
        raise TreatmentSessionV1MintError("TREATMENT_SESSION_LIFETIME_INVALID")

    record = ClinicalAccessSessionRecord(
        session_id=uuid.UUID(capability.session_id),
        patient_id=uuid.UUID(capability.patient_id),
        provider_id=uuid.UUID(capability.provider_id),
        hospital_id=uuid.UUID(capability.hospital_id),
        consent_request_id=capability.request_id,
        token_hash=_lower_sha256(token_hash, code="TREATMENT_TOKEN_HASH_INVALID"),
        purpose=capability.purpose,
        scope=TREATMENT_SESSION_V1_SCOPE,
        allowed_operations=list(capability.allowed_operations),
        provider_session_binding_hash=_lower_sha256(
            capability.provider_session_binding_hash,
            code="TREATMENT_PROVIDER_SESSION_BINDING_INVALID",
        ),
        policy_version=capability.policy_version,
        issued_at=issued_at,
        expires_at=expires_at,
        status="ACTIVE",
        encounter_id=None,
        revoked_at=None,
        revocation_reason=None,
    )
    db.add(record)
    await db.flush()
    return record
