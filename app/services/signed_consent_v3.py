"""Signed Consent V3 canonicalization and exact device-key verification.

V3 is intentionally a separate protocol domain from the legacy signed-approval
V2 contract.  The canonical payload binds the complete server-created consent
context plus the exact logical device and immutable key-version row used to
sign the patient's decision.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_device_keys import PatientDeviceKey
from app.observability.audit_ledger import append_audit_log_or_503
from app.security.audit_context import AuditDomain, current_audit_context

SIGNED_CONSENT_V3_PROTOCOL_VERSION = "nexa-consent-v3"
SIGNED_CONSENT_V3_DOMAIN = "NEXA_CARE_SIGNED_CONSENT"
SIGNED_CONSENT_V3_OPERATION = "CONSENT_DECISION"
_MIN_VERIFY_DURATION_SECONDS = 0.05


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_consent_context_v3(
    *,
    request_id: str,
    patient_id: str,
    provider_id: str,
    hospital_id: str,
    challenge_nonce: str,
    purpose: str,
    scope: str,
    access_duration: int,
    issued_at: str,
    expires_at: str,
) -> bytes:
    """Canonicalize the immutable server-created consent request context."""

    return _canonical_json(
        {
            "access_duration": access_duration,
            "challenge_nonce": challenge_nonce,
            "domain": SIGNED_CONSENT_V3_DOMAIN,
            "expires_at": expires_at,
            "hospital_id": hospital_id,
            "issued_at": issued_at,
            "operation": SIGNED_CONSENT_V3_OPERATION,
            "patient_id": patient_id,
            "protocol_version": SIGNED_CONSENT_V3_PROTOCOL_VERSION,
            "provider_id": provider_id,
            "purpose": purpose,
            "request_id": request_id,
            "scope": scope,
        }
    )


def consent_context_hash_v3(**kwargs) -> str:
    """Return the lowercase SHA-256 anti-substitution digest for V3 context."""

    return hashlib.sha256(canonical_consent_context_v3(**kwargs)).hexdigest()


def canonical_signed_consent_v3_payload(
    *,
    request_id: str,
    patient_id: str,
    provider_id: str,
    hospital_id: str,
    challenge_nonce: str,
    decision: str,
    purpose: str,
    scope: str,
    access_duration: int,
    issued_at: str,
    expires_at: str,
    consent_context_hash: str,
    device_id: str,
    key_id: str,
    key_version: int,
    public_key_fingerprint: str,
) -> bytes:
    """Serialize every V3 decision field as deterministic canonical JSON."""

    return _canonical_json(
        {
            "access_duration": access_duration,
            "challenge_nonce": challenge_nonce,
            "consent_context_hash": consent_context_hash,
            "decision": decision,
            "device_id": device_id,
            "domain": SIGNED_CONSENT_V3_DOMAIN,
            "expires_at": expires_at,
            "hospital_id": hospital_id,
            "issued_at": issued_at,
            "key_id": key_id,
            "key_version": key_version,
            "operation": SIGNED_CONSENT_V3_OPERATION,
            "patient_id": patient_id,
            "protocol_version": SIGNED_CONSENT_V3_PROTOCOL_VERSION,
            "provider_id": provider_id,
            "public_key_fingerprint": public_key_fingerprint,
            "purpose": purpose,
            "request_id": request_id,
            "scope": scope,
        }
    )


@dataclass(frozen=True, slots=True)
class SignedConsentV3Result:
    verified: bool
    patient_id: str
    device_id: str | None = None
    key_id: str | None = None
    key_version: int | None = None
    public_key_fingerprint: str | None = None
    error: str | None = None


class SignedConsentV3Verifier:
    """Verify one V3 decision against one exact currently-active key version."""

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
        scope: str,
        access_duration: int,
        issued_at: str,
        expires_at: str,
        consent_context_hash: str,
        device_id: str,
        key_id: str,
        key_version: int,
        public_key_fingerprint: str,
    ) -> SignedConsentV3Result:
        start_time = time.monotonic()

        if decision not in {"approved", "denied"} or key_version < 1:
            return await self._fail(start_time, patient_id, "Invalid V3 approval context")

        try:
            patient_uuid = uuid.UUID(patient_id)
            logical_device_uuid = uuid.UUID(device_id)
            key_uuid = uuid.UUID(key_id)
        except (TypeError, ValueError):
            return await self._fail(start_time, patient_id, "Invalid V3 device binding")

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

        expected_context_hash = consent_context_hash_v3(
            request_id=request_id,
            patient_id=patient_id,
            provider_id=provider_id,
            hospital_id=hospital_id,
            challenge_nonce=challenge_nonce,
            purpose=purpose,
            scope=scope,
            access_duration=access_duration,
            issued_at=issued_at,
            expires_at=expires_at,
        )
        if not (
            isinstance(consent_context_hash, str)
            and len(consent_context_hash) == 64
            and secrets.compare_digest(
                consent_context_hash.lower(), expected_context_hash
            )
        ):
            return await self._fail(
                start_time, patient_id, "Consent context integrity failure"
            )

        if not (
            isinstance(public_key_fingerprint, str)
            and len(public_key_fingerprint) == 64
            and public_key_fingerprint == public_key_fingerprint.lower()
        ):
            return await self._fail(start_time, patient_id, "Invalid V3 device binding")

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

        signing_input = canonical_signed_consent_v3_payload(
            request_id=request_id,
            patient_id=patient_id,
            provider_id=provider_id,
            hospital_id=hospital_id,
            challenge_nonce=challenge_nonce,
            decision=decision,
            purpose=purpose,
            scope=scope,
            access_duration=access_duration,
            issued_at=issued_at,
            expires_at=expires_at,
            consent_context_hash=consent_context_hash,
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
        return SignedConsentV3Result(
            verified=True,
            patient_id=patient_id,
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
            metadata={"reason": reason, "protocol_version": SIGNED_CONSENT_V3_PROTOCOL_VERSION},
        )

    async def _fail(
        self, start_time: float, patient_id: str, reason: str
    ) -> SignedConsentV3Result:
        await self._pad_time(start_time)
        return SignedConsentV3Result(verified=False, patient_id=patient_id, error=reason)

    async def _pad_time(self, start_time: float) -> None:
        remaining = _MIN_VERIFY_DURATION_SECONDS - (time.monotonic() - start_time)
        if remaining > 0:
            await asyncio.sleep(remaining)
