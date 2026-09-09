"""PostgreSQL transactions for Slice 6E patient device recovery.

These mutations deliberately reuse the Slice 6C/6D device schema and audit
vocabulary. Recovery creates a fresh logical device and never reconstructs or
re-activates historical private-key authority.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_device_keys import PatientDeviceKey, PatientDeviceKeyStatus
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.audit_outbox import enqueue_audit_event
from app.services.patient_device_rotation import verify_device_rotation_signature
from app.services.patient_device_trust import (
    MAX_ACTIVE_PATIENT_DEVICES,
    PatientDeviceTrustError,
    canonicalize_p256_public_key,
)


@dataclass(frozen=True, slots=True)
class PatientRecoveryResult:
    device_id: uuid.UUID
    key_id: uuid.UUID
    key_version: int
    public_key_fingerprint: str
    enrolled_at: datetime
    revoked_device_count: int
    status: str


def _is_postgresql(db: AsyncSession) -> bool:
    bind = db.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


def _patient_lock_key(patient_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"nexa:patient-device:{patient_id}".encode()).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def _lock_patient_device_set(db: AsyncSession, patient_id: uuid.UUID) -> None:
    if _is_postgresql(db):
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": _patient_lock_key(patient_id)},
        )


def _audit_key(action: str, row_id: uuid.UUID) -> str:
    return f"patient-device:{action}:{row_id}"


async def patient_has_device_history(
    db: AsyncSession, *, patient_id: uuid.UUID
) -> bool:
    row_id = await db.scalar(
        select(PatientDeviceKey.id)
        .where(PatientDeviceKey.patient_id == patient_id)
        .limit(1)
    )
    return row_id is not None


async def _assert_new_key_available(
    db: AsyncSession, *, public_key_fingerprint: str
) -> None:
    existing = await db.scalar(
        select(PatientDeviceKey).where(
            PatientDeviceKey.public_key_fingerprint == public_key_fingerprint
        )
    )
    if existing is None:
        return
    if existing.status in {
        PatientDeviceKeyStatus.REVOKED.value,
        PatientDeviceKeyStatus.REPLACED.value,
        PatientDeviceKeyStatus.COMPROMISED.value,
    }:
        raise PatientDeviceTrustError("DEVICE_KEY_RESURRECTION_FORBIDDEN")
    raise PatientDeviceTrustError("DEVICE_KEY_ALREADY_ENROLLED")


async def enroll_patient_device_from_trusted_authorizer(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    authorizer_device_id: uuid.UUID,
    expected_authorizer_key_version: int,
    raw_new_public_key: bytes,
    signing_payload: bytes,
    signature_b64: str,
    device_label: str | None,
    platform: str,
    actor_id: str,
) -> PatientDeviceKey:
    """Enroll a fresh logical device only when a current device proves possession."""

    canonical = canonicalize_p256_public_key(raw_new_public_key)
    now = datetime.now(timezone.utc)
    new_device_id = uuid.uuid4()
    try:
        async with db.begin():
            await _lock_patient_device_set(db, patient_id)
            authorizer = await db.scalar(
                select(PatientDeviceKey)
                .where(
                    PatientDeviceKey.patient_id == patient_id,
                    PatientDeviceKey.device_id == authorizer_device_id,
                    PatientDeviceKey.status == PatientDeviceKeyStatus.ACTIVE.value,
                    PatientDeviceKey.revoked_at.is_(None),
                )
                .with_for_update()
            )
            if authorizer is None:
                historical = await db.scalar(
                    select(PatientDeviceKey.id)
                    .where(
                        PatientDeviceKey.patient_id == patient_id,
                        PatientDeviceKey.device_id == authorizer_device_id,
                    )
                    .limit(1)
                )
                raise PatientDeviceTrustError(
                    "DEVICE_NOT_ACTIVE" if historical is not None else "DEVICE_NOT_FOUND"
                )
            if authorizer.key_version != expected_authorizer_key_version:
                raise PatientDeviceTrustError("DEVICE_KEY_VERSION_STALE")
            if not verify_device_rotation_signature(
                public_key_der=authorizer.device_public_key,
                signing_payload=signing_payload,
                signature_b64=signature_b64,
            ):
                raise PatientDeviceTrustError("TRUSTED_ENROLLMENT_SIGNATURE_INVALID")

            await _assert_new_key_available(
                db, public_key_fingerprint=canonical.fingerprint
            )
            active_count = await db.scalar(
                select(func.count(PatientDeviceKey.id)).where(
                    PatientDeviceKey.patient_id == patient_id,
                    PatientDeviceKey.status == PatientDeviceKeyStatus.ACTIVE.value,
                    PatientDeviceKey.revoked_at.is_(None),
                )
            )
            if int(active_count or 0) >= MAX_ACTIVE_PATIENT_DEVICES:
                raise PatientDeviceTrustError("DEVICE_ACTIVE_LIMIT_REACHED")

            row = PatientDeviceKey(
                patient_id=patient_id,
                device_id=new_device_id,
                key_version=1,
                device_public_key=canonical.der,
                public_key_fingerprint=canonical.fingerprint,
                device_label=device_label,
                platform=platform,
                key_algorithm="ECDSA-P256",
                status=PatientDeviceKeyStatus.ACTIVE.value,
                enrolled_at=now,
                revoked_at=None,
            )
            db.add(row)
            await db.flush()
            await enqueue_audit_event(
                db,
                audit_context=current_audit_context(AuditDomain.PLATFORM),
                idempotency_key=_audit_key("trusted-enrolled", row.id),
                actor_id=actor_id,
                event_type="DEVICE_KEY_ENROLLED",
                target_id=str(row.device_id),
                patient_id=str(patient_id),
                status="SUCCESS",
                metadata={
                    "operation": "trusted_device_enrollment",
                    "key_version": 1,
                    "platform": platform,
                    "authorized_by_device_id": str(authorizer_device_id),
                    "authorized_by_key_version": authorizer.key_version,
                },
            )
        return row
    except IntegrityError as exc:
        await db.rollback()
        raise PatientDeviceTrustError("DEVICE_KEY_ALREADY_ENROLLED") from exc


async def recover_patient_device_authority(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    raw_new_public_key: bytes,
    device_label: str | None,
    platform: str,
    actor_id: str,
) -> PatientRecoveryResult:
    """Revoke all current device authority and install one fresh logical device."""

    canonical = canonicalize_p256_public_key(raw_new_public_key)
    now = datetime.now(timezone.utc)
    new_device_id = uuid.uuid4()
    try:
        async with db.begin():
            await _lock_patient_device_set(db, patient_id)
            history_count = await db.scalar(
                select(func.count(PatientDeviceKey.id)).where(
                    PatientDeviceKey.patient_id == patient_id
                )
            )
            if int(history_count or 0) == 0:
                raise PatientDeviceTrustError("DEVICE_RECOVERY_NOT_REQUIRED")

            await _assert_new_key_available(
                db, public_key_fingerprint=canonical.fingerprint
            )
            active_rows = (
                (
                    await db.execute(
                        select(PatientDeviceKey)
                        .where(
                            PatientDeviceKey.patient_id == patient_id,
                            PatientDeviceKey.status
                            == PatientDeviceKeyStatus.ACTIVE.value,
                            PatientDeviceKey.revoked_at.is_(None),
                        )
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            for row in active_rows:
                row.status = PatientDeviceKeyStatus.REVOKED.value
                row.revoked_at = now
                row.revocation_reason_code = "ACCOUNT_RECOVERY"
                row.revocation_actor = "patient_recovery"
            await db.flush()

            for row in active_rows:
                await enqueue_audit_event(
                    db,
                    audit_context=current_audit_context(AuditDomain.PLATFORM),
                    idempotency_key=_audit_key("recovery-revoked", row.id),
                    actor_id=actor_id,
                    event_type="DEVICE_KEY_REVOKED",
                    target_id=str(row.device_id),
                    patient_id=str(patient_id),
                    status="SUCCESS",
                    metadata={
                        "operation": "account_recovery",
                        "key_version": row.key_version,
                        "reason_code": "ACCOUNT_RECOVERY",
                    },
                )

            new_row = PatientDeviceKey(
                patient_id=patient_id,
                device_id=new_device_id,
                key_version=1,
                device_public_key=canonical.der,
                public_key_fingerprint=canonical.fingerprint,
                device_label=device_label,
                platform=platform,
                key_algorithm="ECDSA-P256",
                status=PatientDeviceKeyStatus.ACTIVE.value,
                enrolled_at=now,
                revoked_at=None,
            )
            db.add(new_row)
            await db.flush()
            await enqueue_audit_event(
                db,
                audit_context=current_audit_context(AuditDomain.PLATFORM),
                idempotency_key=_audit_key("recovery-enrolled", new_row.id),
                actor_id=actor_id,
                event_type="DEVICE_KEY_ENROLLED",
                target_id=str(new_row.device_id),
                patient_id=str(patient_id),
                status="SUCCESS",
                metadata={
                    "operation": "account_recovery",
                    "key_version": 1,
                    "platform": platform,
                    "revoked_device_count": len(active_rows),
                },
            )
        return PatientRecoveryResult(
            device_id=new_row.device_id,
            key_id=new_row.id,
            key_version=1,
            public_key_fingerprint=canonical.fingerprint,
            enrolled_at=now,
            revoked_device_count=len(active_rows),
            status=PatientDeviceKeyStatus.ACTIVE.value,
        )
    except IntegrityError as exc:
        await db.rollback()
        raise PatientDeviceTrustError("DEVICE_KEY_ALREADY_ENROLLED") from exc
