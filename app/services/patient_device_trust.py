"""Transactional patient cryptographic-device trust lifecycle.

The backend stores public keys only. A logical device has a stable server-owned
``device_id`` and one or more immutable key-version rows. Slice 6C establishes
version 1 enrollment and terminal revocation semantics; Slice 6D adds normal
key rotation using the same lineage fields.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient_device_keys import PatientDeviceKey, PatientDeviceKeyStatus
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.audit_outbox import enqueue_audit_event

MAX_ACTIVE_PATIENT_DEVICES = 5


class PatientDeviceTrustError(ValueError):
    """Deterministic, non-sensitive device-trust denial."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CanonicalPatientPublicKey:
    der: bytes
    fingerprint: str


def canonicalize_p256_public_key(raw_key: bytes) -> CanonicalPatientPublicKey:
    """Validate and canonicalize an ECDSA P-256 SubjectPublicKeyInfo key."""

    try:
        public_key = serialization.load_der_public_key(raw_key)
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise PatientDeviceTrustError("DEVICE_PUBLIC_KEY_INVALID") from exc
    if not (
        isinstance(public_key, ec.EllipticCurvePublicKey)
        and isinstance(public_key.curve, ec.SECP256R1)
    ):
        raise PatientDeviceTrustError("DEVICE_PUBLIC_KEY_NOT_P256")
    canonical_der = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return CanonicalPatientPublicKey(
        der=canonical_der,
        fingerprint=hashlib.sha256(canonical_der).hexdigest(),
    )


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


async def enroll_patient_device_key(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    raw_public_key: bytes,
    device_label: str | None,
    platform: str,
    actor_id: str,
) -> PatientDeviceKey:
    """Enroll version 1 of a new logical device under DB-safe invariants.

    A per-patient PostgreSQL transaction advisory lock serializes the active
    device-set count. Global fingerprint uniqueness is additionally enforced by
    a database unique index so different-patient races cannot reuse one key.
    """

    canonical = canonicalize_p256_public_key(raw_public_key)
    new_device_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    try:
        async with db.begin():
            await _lock_patient_device_set(db, patient_id)

            existing = await db.scalar(
                select(PatientDeviceKey).where(
                    PatientDeviceKey.public_key_fingerprint == canonical.fingerprint
                )
            )
            if existing is not None:
                if existing.status in {
                    PatientDeviceKeyStatus.REVOKED.value,
                    PatientDeviceKeyStatus.REPLACED.value,
                    PatientDeviceKeyStatus.COMPROMISED.value,
                }:
                    raise PatientDeviceTrustError("DEVICE_KEY_RESURRECTION_FORBIDDEN")
                raise PatientDeviceTrustError("DEVICE_KEY_ALREADY_ENROLLED")

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
                idempotency_key=_audit_key("enrolled", row.id),
                actor_id=actor_id,
                event_type="DEVICE_KEY_ENROLLED",
                target_id=str(row.device_id),
                patient_id=str(patient_id),
                status="SUCCESS",
                metadata={"key_version": 1, "platform": platform},
            )
        return row
    except IntegrityError as exc:
        await db.rollback()
        raise PatientDeviceTrustError("DEVICE_KEY_ALREADY_ENROLLED") from exc


async def revoke_patient_device(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    device_id: uuid.UUID,
    actor_id: str,
    reason_code: str = "PATIENT_REVOKED",
    actor_context: str = "patient_current_session",
) -> PatientDeviceKey:
    """Terminally revoke the current key version for a logical device."""

    now = datetime.now(timezone.utc)
    async with db.begin():
        await _lock_patient_device_set(db, patient_id)
        row = await db.scalar(
            select(PatientDeviceKey)
            .where(
                PatientDeviceKey.patient_id == patient_id,
                PatientDeviceKey.device_id == device_id,
                PatientDeviceKey.status == PatientDeviceKeyStatus.ACTIVE.value,
                PatientDeviceKey.revoked_at.is_(None),
            )
            .with_for_update()
        )
        if row is None:
            historical = await db.scalar(
                select(PatientDeviceKey).where(
                    PatientDeviceKey.patient_id == patient_id,
                    PatientDeviceKey.device_id == device_id,
                )
            )
            if historical is None:
                raise PatientDeviceTrustError("DEVICE_NOT_FOUND")
            raise PatientDeviceTrustError("DEVICE_NOT_ACTIVE")

        row.status = PatientDeviceKeyStatus.REVOKED.value
        row.revoked_at = now
        row.revocation_reason_code = reason_code
        row.revocation_actor = actor_context
        await db.flush()
        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.PLATFORM),
            idempotency_key=_audit_key("revoked", row.id),
            actor_id=actor_id,
            event_type="DEVICE_KEY_REVOKED",
            target_id=str(device_id),
            patient_id=str(patient_id),
            status="SUCCESS",
            metadata={"key_version": row.key_version, "reason_code": reason_code},
        )
    return row
