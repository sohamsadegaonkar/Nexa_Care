"""Patient-signed treatment-session V1 protocol primitives.

This protocol is deliberately separate from Signed Consent V3. V3 predates the
bounded clinical-session model and signs purpose/scope rather than an explicit
clinical operation set. Treatment-session V1 therefore has its own protocol
version/domain and binds the exact server-owned operation set before any future
write gate may consume it.

This module does not mint authority or enable clinical writes by itself. It
only defines deterministic signed bytes and exact active-device verification
for the operation-bound context that later Slice 10B stages can consume.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Final

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_device_keys import PatientDeviceKey
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, current_audit_context
from app.security.clinical_access_policy import (
    CLINICAL_ACCESS_POLICY_VERSION,
    ClinicalAccessOperation,
)

SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION: Final[str] = "nexa-treatment-session-v1"
SIGNED_TREATMENT_SESSION_V1_DOMAIN: Final[str] = "NEXA_CARE_SIGNED_TREATMENT_SESSION"
SIGNED_TREATMENT_SESSION_V1_OPERATION: Final[str] = "TREATMENT_SESSION_DECISION"
_MIN_ACCESS_DURATION_SECONDS: Final[int] = 300
_MAX_ACCESS_DURATION_SECONDS: Final[int] = 3600
_MIN_VERIFY_DURATION_SECONDS = 0.05
_ALLOWED_OPERATION_VALUES: Final[frozenset[str]] = frozenset(
    operation.value for operation in ClinicalAccessOperation
)


class TreatmentSessionV1ProtocolError(ValueError):
    """Raised when a treatment-session context is not safe to canonicalize."""


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def normalize_treatment_operations(
    operations: Sequence[str | ClinicalAccessOperation],
) -> tuple[str, ...]:
    """Validate and deterministically canonicalize one exact operation set.

    The operation set is semantically unordered, so canonical bytes sort it.
    Duplicates are rejected rather than silently collapsed because accepting
    ambiguous caller input at an authority boundary makes review and replay
    reasoning harder. Unknown operations fail closed.
    """

    if isinstance(operations, (str, bytes)):
        raise TreatmentSessionV1ProtocolError("operation set must be a sequence")
    if not operations:
        raise TreatmentSessionV1ProtocolError("at least one treatment operation is required")

    normalized: list[str] = []
    seen: set[str] = set()
    for operation in operations:
        if isinstance(operation, ClinicalAccessOperation):
            value = operation.value
        elif isinstance(operation, str):
            value = operation
        else:
            raise TreatmentSessionV1ProtocolError("treatment operation must be a string")
        if value not in _ALLOWED_OPERATION_VALUES:
            raise TreatmentSessionV1ProtocolError("unknown treatment operation")
        if value in seen:
            raise TreatmentSessionV1ProtocolError("duplicate treatment operation")
        seen.add(value)
        normalized.append(value)

    return tuple(sorted(normalized))


def _validate_context_inputs(*, purpose: str, access_duration: int) -> None:
    if not isinstance(purpose, str) or not purpose or purpose != purpose.strip():
        raise TreatmentSessionV1ProtocolError("treatment purpose must be a canonical non-empty string")
    if isinstance(access_duration, bool) or not isinstance(access_duration, int):
        raise TreatmentSessionV1ProtocolError("access duration must be an integer")
    if not _MIN_ACCESS_DURATION_SECONDS <= access_duration <= _MAX_ACCESS_DURATION_SECONDS:
        raise TreatmentSessionV1ProtocolError("access duration is outside the treatment-session policy")


def canonical_treatment_context_v1(
    *,
    request_id: str,
    patient_id: str,
    provider_id: str,
    hospital_id: str,
    challenge_nonce: str,
    purpose: str,
    allowed_operations: Sequence[str | ClinicalAccessOperation],
    access_duration: int,
    issued_at: str,
    expires_at: str,
) -> bytes:
    """Canonicalize the immutable operation-bound treatment request context."""

    _validate_context_inputs(purpose=purpose, access_duration=access_duration)
    operations = normalize_treatment_operations(allowed_operations)
    return _canonical_json(
        {
            "access_duration": access_duration,
            "allowed_operations": list(operations),
            "challenge_nonce": challenge_nonce,
            "domain": SIGNED_TREATMENT_SESSION_V1_DOMAIN,
            "expires_at": expires_at,
            "hospital_id": hospital_id,
            "issued_at": issued_at,
            "operation": SIGNED_TREATMENT_SESSION_V1_OPERATION,
            "patient_id": patient_id,
            "policy_version": CLINICAL_ACCESS_POLICY_VERSION,
            "protocol_version": SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
            "provider_id": provider_id,
            "purpose": purpose,
            "request_id": request_id,
        }
    )


def treatment_context_hash_v1(**kwargs) -> str:
    """Return the lowercase SHA-256 anti-substitution digest for V1 context."""

    return hashlib.sha256(canonical_treatment_context_v1(**kwargs)).hexdigest()


def canonical_signed_treatment_v1_payload(
    *,
    request_id: str,
    patient_id: str,
    provider_id: str,
    hospital_id: str,
    challenge_nonce: str,
    decision: str,
    purpose: str,
    allowed_operations: Sequence[str | ClinicalAccessOperation],
    access_duration: int,
    issued_at: str,
    expires_at: str,
    treatment_context_hash: str,
    device_id: str,
    key_id: str,
    key_version: int,
    public_key_fingerprint: str,
) -> bytes:
    """Serialize every treatment-session decision field as canonical JSON."""

    _validate_context_inputs(purpose=purpose, access_duration=access_duration)
    operations = normalize_treatment_operations(allowed_operations)
    return _canonical_json(
        {
            "access_duration": access_duration,
            "allowed_operations": list(operations),
            "challenge_nonce": challenge_nonce,
            "decision": decision,
            "device_id": device_id,
            "domain": SIGNED_TREATMENT_SESSION_V1_DOMAIN,
            "expires_at": expires_at,
            "hospital_id": hospital_id,
            "issued_at": issued_at,
            "key_id": key_id,
            "key_version": key_version,
            "operation": SIGNED_TREATMENT_SESSION_V1_OPERATION,
            "patient_id": patient_id,
            "policy_version": CLINICAL_ACCESS_POLICY_VERSION,
            "protocol_version": SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
            "provider_id": provider_id,
            "public_key_fingerprint": public_key_fingerprint,
            "purpose": purpose,
            "request_id": request_id,
            "treatment_context_hash": treatment_context_hash,
        }
    )


@dataclass(frozen=True, slots=True)
class SignedTreatmentSessionV1Result:
    verified: bool
    patient_id: str
    allowed_operations: tuple[str, ...] = ()
    device_id: str | None = None
    key_id: str | None = None
    key_version: int | None = None
    public_key_fingerprint: str | None = None
    error: str | None = None


class SignedTreatmentSessionV1Verifier:
    """Verify one operation-bound decision against one exact active key row."""

    async def verify(
        self,
        *,
        db: AsyncSession,
        patient_id: str,
        request_id: str,
        provider_id: str,
        hospital_id: str,
        challenge_nonce: str,
        decision: str,
        signature_b64: str,
        purpose: str,
        allowed_operations: Sequence[str | ClinicalAccessOperation],
        access_duration: int,
        issued_at: str,
        expires_at: str,
        treatment_context_hash: str,
        device_id: str,
        key_id: str,
        key_version: int,
        public_key_fingerprint: str,
    ) -> SignedTreatmentSessionV1Result:
        start_time = time.monotonic()

        if decision not in {"approved", "denied"} or key_version < 1:
            return await self._fail(start_time, patient_id, "Invalid treatment approval context")

        try:
            operations = normalize_treatment_operations(allowed_operations)
            _validate_context_inputs(purpose=purpose, access_duration=access_duration)
            patient_uuid = uuid.UUID(patient_id)
            logical_device_uuid = uuid.UUID(device_id)
            key_uuid = uuid.UUID(key_id)
        except (TreatmentSessionV1ProtocolError, TypeError, ValueError):
            return await self._fail(start_time, patient_id, "Invalid treatment approval context")

        try:
            issued_dt = datetime.fromisoformat(issued_at.replace("Z", "+00:00"))
            expires_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if (
                issued_dt.tzinfo is None
                or issued_dt.utcoffset() is None
                or expires_dt.tzinfo is None
                or expires_dt.utcoffset() is None
            ):
                raise ValueError("timezone required")
            now = datetime.now(timezone.utc)
            if issued_dt > now or now >= expires_dt or issued_dt >= expires_dt:
                return await self._fail(start_time, patient_id, "Challenge expired")
        except (TypeError, ValueError):
            return await self._fail(start_time, patient_id, "Invalid challenge expiry")

        expected_context_hash = treatment_context_hash_v1(
            request_id=request_id,
            patient_id=patient_id,
            provider_id=provider_id,
            hospital_id=hospital_id,
            challenge_nonce=challenge_nonce,
            purpose=purpose,
            allowed_operations=operations,
            access_duration=access_duration,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        if not (
            isinstance(treatment_context_hash, str)
            and len(treatment_context_hash) == 64
            and secrets.compare_digest(treatment_context_hash.lower(), expected_context_hash)
        ):
            return await self._fail(start_time, patient_id, "Treatment context integrity failure")

        if not (
            isinstance(public_key_fingerprint, str)
            and len(public_key_fingerprint) == 64
            and public_key_fingerprint == public_key_fingerprint.lower()
        ):
            return await self._fail(start_time, patient_id, "Invalid treatment device binding")

        try:
            raw_signature = base64.b64decode(signature_b64, validate=True)
        except Exception:
            await self._audit_failure(patient_id, request_id, "invalid_base64_signature")
            return await self._fail(start_time, patient_id, "Signature verification failed")

        result = await db.execute(
            select(PatientDeviceKey).where(
                PatientDeviceKey.id == key_uuid,
                PatientDeviceKey.patient_id == patient_uuid,
                PatientDeviceKey.device_id == logical_device_uuid,
                PatientDeviceKey.key_version == key_version,
                PatientDeviceKey.public_key_fingerprint == public_key_fingerprint,
                PatientDeviceKey.status == "active",
                PatientDeviceKey.revoked_at.is_(None),
            )
        )
        key_row = result.scalar_one_or_none()
        if key_row is None:
            await self._audit_failure(patient_id, request_id, "exact_key_version_not_active")
            return await self._fail(start_time, patient_id, "Signature verification failed")

        signing_input = canonical_signed_treatment_v1_payload(
            request_id=request_id,
            patient_id=patient_id,
            provider_id=provider_id,
            hospital_id=hospital_id,
            challenge_nonce=challenge_nonce,
            decision=decision,
            purpose=purpose,
            allowed_operations=operations,
            access_duration=access_duration,
            issued_at=issued_at,
            expires_at=expires_at,
            treatment_context_hash=treatment_context_hash,
            device_id=device_id,
            key_id=key_id,
            key_version=key_version,
            public_key_fingerprint=public_key_fingerprint,
        )

        try:
            public_key = serialization.load_der_public_key(key_row.device_public_key)
            if not isinstance(public_key, ec.EllipticCurvePublicKey) or not isinstance(
                public_key.curve, ec.SECP256R1
            ):
                raise ValueError("unexpected key type")
            public_key.verify(
                raw_signature,
                hashlib.sha256(signing_input).digest(),
                ec.ECDSA(utils.Prehashed(hashes.SHA256())),
            )
        except Exception:
            await self._audit_failure(patient_id, request_id, "key_mismatch")
            return await self._fail(start_time, patient_id, "Signature verification failed")

        await self._pad_time(start_time)
        return SignedTreatmentSessionV1Result(
            verified=True,
            patient_id=patient_id,
            allowed_operations=operations,
            device_id=str(key_row.device_id),
            key_id=str(key_row.id),
            key_version=key_row.key_version,
            public_key_fingerprint=key_row.public_key_fingerprint,
        )

    async def _audit_failure(self, patient_id: str, request_id: str, reason: str) -> None:
        await append_audit_log_or_503(
            audit_context=current_audit_context(AuditDomain.CONSENT),
            actor_uid=patient_id,
            event_type="SIGNATURE_VERIFICATION_FAILED",
            target_id=request_id,
            status="FAILED",
            metadata={
                "reason": reason,
                "protocol_version": SIGNED_TREATMENT_SESSION_V1_PROTOCOL_VERSION,
            },
        )

    async def _fail(
        self, start_time: float, patient_id: str, reason: str
    ) -> SignedTreatmentSessionV1Result:
        await self._pad_time(start_time)
        return SignedTreatmentSessionV1Result(
            verified=False,
            patient_id=patient_id,
            error=reason,
        )

    async def _pad_time(self, start_time: float) -> None:
        remaining = _MIN_VERIFY_DURATION_SECONDS - (time.monotonic() - start_time)
        if remaining > 0:
            await asyncio.sleep(remaining)
