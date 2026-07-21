"""Service for verifying cryptographic signed approvals from patient devices (Workstream 2).

Enforces fixed-time verification to prevent timing side-channels between
'no key enrolled' and 'key mismatch'.
"""

from __future__ import annotations

from app.security.audit_context import AuditDomain, current_audit_context

import asyncio
import base64
import hashlib
import json
import logging
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

logger = logging.getLogger("nexa_logger")

_MIN_VERIFY_DURATION_SECONDS = 0.05
SIGNED_APPROVAL_PROTOCOL_VERSION = "nexa-consent-v2"


def canonical_signed_approval_payload(
    *, request_id: str, patient_id: str, provider_id: str,
    challenge_nonce: str, decision: str, purpose: str, scope: str,
    issued_at: str, expires_at: str, access_duration: int, device_id: str,
) -> bytes:
    """Serialize every security-relevant approval field as canonical JSON."""
    payload = {
        "access_duration": access_duration,
        "challenge_nonce": challenge_nonce,
        "decision": decision,
        "device_id": device_id,
        "expires_at": expires_at,
        "issued_at": issued_at,
        "patient_id": patient_id,
        "protocol_version": SIGNED_APPROVAL_PROTOCOL_VERSION,
        "provider_id": provider_id,
        "purpose": purpose,
        "request_id": request_id,
        "scope": scope,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@dataclass(frozen=True, slots=True)
class SignedApprovalResult:
    verified: bool
    patient_id: str
    matched_device_id: str | None = None
    error: str | None = None


class SignedApprovalVerifier:
    """Verifies ECDSA P-256 signatures submitted by patient mobile devices over challenge payloads."""

    async def verify_signed_approval(
        self,
        db: AsyncSession,
        patient_id: str,
        request_id: str,
        challenge_nonce: str,
        decision: str,
        signature_b64: str,
        expires_at: str,
        issued_at: str,
        provider_id: str | None = None,
        scope: str | None = None,
        purpose: str | None = None,
        access_duration: int | None = None,
        device_id: str | None = None,
    ) -> SignedApprovalResult:
        """Verify ECDSA signature against the submitted active device key.

        Enforces a minimum execution duration (_MIN_VERIFY_DURATION_SECONDS) to prevent timing attacks.
        """
        start_time = time.monotonic()

        try:
            pid_uuid = uuid.UUID(patient_id)
        except ValueError:
            return await self._fail(start_time, patient_id, "Invalid patient identifier")

        # 1. Check expiration timestamp
        try:
            exp_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if exp_dt.tzinfo is None:
                return await self._fail(start_time, patient_id, "Invalid challenge expiry")
            if datetime.now(timezone.utc) > exp_dt:
                return await self._fail(start_time, patient_id, "Challenge expired")
        except Exception:
            return await self._fail(start_time, patient_id, "Invalid challenge expiry")

        if not all((provider_id, scope, purpose, issued_at, device_id)) or access_duration is None:
            return await self._fail(start_time, patient_id, "Incomplete signed approval context")
        signing_input = canonical_signed_approval_payload(
            request_id=request_id, patient_id=patient_id, provider_id=provider_id,
            challenge_nonce=challenge_nonce, decision=decision, purpose=purpose,
            scope=scope, issued_at=issued_at, expires_at=expires_at,
            access_duration=access_duration, device_id=device_id,
        )

        # 3. Decode base64 signature
        try:
            raw_sig = base64.b64decode(signature_b64, validate=True)
        except Exception:
            await append_audit_log_or_503(
                audit_context=current_audit_context(AuditDomain.CONSENT),
                actor_uid=patient_id,
                event_type="SIGNATURE_VERIFICATION_FAILED",
                target_id=request_id,
                status="FAILED",
                metadata={"reason": "invalid_base64_signature"},
            )
            return await self._fail(start_time, patient_id, "Signature verification failed")

        # 4. Fetch patient's active device public keys
        stmt = select(PatientDeviceKey).where(
            PatientDeviceKey.patient_id == pid_uuid,
            PatientDeviceKey.status == "active",
            PatientDeviceKey.revoked_at.is_(None),
        )
        if device_id is not None:
            try:
                stmt = stmt.where(PatientDeviceKey.id == uuid.UUID(device_id))
            except ValueError:
                return await self._fail(start_time, patient_id, "Signature verification failed")
        res = await db.execute(stmt)
        keys = res.scalars().all()

        if not keys:
            await append_audit_log_or_503(
                audit_context=current_audit_context(AuditDomain.CONSENT),
                actor_uid=patient_id,
                event_type="SIGNATURE_VERIFICATION_FAILED",
                target_id=request_id,
                status="FAILED",
                metadata={"reason": "no_enrolled_active_devices"},
            )
            return await self._fail(start_time, patient_id, "Signature verification failed")

        # 5. Verify against each active key
        matched_device_id = None
        for dev_key in keys:
            if device_id is not None and str(dev_key.id) != device_id:
                continue
            try:
                pub_key = serialization.load_der_public_key(dev_key.device_public_key)
                if not isinstance(pub_key, ec.EllipticCurvePublicKey):
                    continue
                pub_key.verify(
                    raw_sig,
                    hashlib.sha256(signing_input).digest(),
                    ec.ECDSA(utils.Prehashed(hashes.SHA256())),
                )
                matched_device_id = str(dev_key.id)
                break
            except Exception:
                continue

        if not matched_device_id:
            await append_audit_log_or_503(
                audit_context=current_audit_context(AuditDomain.CONSENT),
                actor_uid=patient_id,
                event_type="SIGNATURE_VERIFICATION_FAILED",
                target_id=request_id,
                status="FAILED",
                metadata={"reason": "key_mismatch"},
            )
            return await self._fail(start_time, patient_id, "Signature verification failed")

        await self._pad_time(start_time)
        return SignedApprovalResult(verified=True, patient_id=patient_id, matched_device_id=matched_device_id)

    async def _fail(self, start_time: float, patient_id: str, reason: str) -> SignedApprovalResult:
        await self._pad_time(start_time)
        return SignedApprovalResult(verified=False, patient_id=patient_id, error=reason)

    async def _pad_time(self, start_time: float) -> None:
        elapsed = time.monotonic() - start_time
        remaining = _MIN_VERIFY_DURATION_SECONDS - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)
