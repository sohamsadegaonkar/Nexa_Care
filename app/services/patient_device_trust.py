"""Transactional patient cryptographic-device trust lifecycle.

The backend stores public keys only. A logical device has a stable server-owned
``device_id`` and one or more immutable key-version rows. Slice 6C establishes
version 1 enrollment and terminal revocation semantics; Slice 6D adds normal
proof-of-possession key rotation using the same lineage fields.
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
from app.services.patient_device_rotation import verify_device_rotation_signature

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


@dataclass(frozen=True, slots=True)
class PatientDeviceRotationResult:
    device_id: uuid.UUID
    old_key_id: uuid.UUID
    new_key_id: uuid.UUID
    old_key_version: int
    new_key_version: int
    new_public_key_fingerprint: str
    rotated_at: datetime
    status: str


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


async def _historical_device_exists(
    db: AsyncSession, *, patient_id: uuid.UUID, device_id: uuid.UUID
) -> bool:
    historical = await db.scalar(
        select(PatientDeviceKey.id)
        .where(
            PatientDeviceKey.patient_id == patient_id,
            PatientDeviceKey.device_id == device_id,
        )
        .limit(1)
    )
    return historical is not None


async def get_active_patient_device_key(
    db: AsyncSession, *, patient_id: uuid.UUID, device_id: uuid.UUID
) -> PatientDeviceKey:
    """Resolve the one active key version for a patient-owned logical device."""

    row = await db.scalar(
        select(PatientDeviceKey).where(
            PatientDeviceKey.patient_id == patient_id,
            PatientDeviceKey.device_id == device_id,
            PatientDeviceKey.status == PatientDeviceKeyStatus.ACTIVE.value,
            PatientDeviceKey.revoked_at.is_(None),
        )
    )
    if row is not None:
        return row
    if await _historical_device_exists(db, patient_id=patient_id, device_id=device_id):
        raise PatientDeviceTrustError("DEVICE_NOT_ACTIVE")
    raise PatientDeviceTrustError("DEVICE_NOT_FOUND")


async def assert_rotation_new_key_available(
    db: AsyncSession, *, public_key_fingerprint: str
) -> None:
    """Fail early when proposed rotation key material already has ownership."""

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


async def enroll_patient_device_key(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    raw_public_key: bytes,
    device_label: str | None,
    platform: str,
    actor_id: str,
    require_no_history: bool = False,
) -> PatientDeviceKey:
    """Enroll version 1 of a new logical device under DB-safe invariants.

    A per-patient PostgreSQL transaction advisory lock serializes the active
    device-set count. Global fingerprint uniqueness is additionally enforced by
    a database unique index so different-patient races cannot reuse one key.

    ``require_no_history`` is the bootstrap-only guard. When true, the same
    transaction lock also proves the patient has no historical device row before
    insertion. This closes the route-level check/use race where two distinct
    bootstrap grants could otherwise both observe an empty device set and then
    serialize into two trusted devices.
    """

    canonical = canonicalize_p256_public_key(raw_public_key)
    new_device_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    try:
        async with db.begin():
            await _lock_patient_device_set(db, patient_id)

            if require_no_history:
                historical = await db.scalar(
                    select(PatientDeviceKey.id)
                    .where(PatientDeviceKey.patient_id == patient_id)
                    .limit(1)
                )
                if historical is not None:
                    raise PatientDeviceTrustError("DEVICE_RECOVERY_REQUIRED")

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


async def rotate_patient_device_key(
    db: AsyncSession,
    *,
    patient_id: uuid.UUID,
    device_id: uuid.UUID,
    expected_key_version: int,
    raw_new_public_key: bytes,
    signing_payload: bytes,
    signature_b64: str,
    actor_id: str,
) -> PatientDeviceRotationResult:
    """Advance one logical device to the next immutable key version atomically.

    The old active row is locked and must exactly match ``expected_key_version``.
    Its public key verifies the rotation proof before any authority mutation.
    The old row then becomes terminal ``replaced`` and the new active row is
    linked bidirectionally in the same PostgreSQL transaction as both lifecycle
    audit-outbox events.
    """

    canonical = canonicalize_p256_public_key(raw_new_public_key)
    now = datetime.now(timezone.utc)
    new_key_id = uuid.uuid4()

    try:
        async with db.begin():
            await _lock_patient_device_set(db, patient_id)
            old = await db.scalar(
                select(PatientDeviceKey)
                .where(
                    PatientDeviceKey.patient_id == patient_id,
                    PatientDeviceKey.device_id == device_id,
                    PatientDeviceKey.status == PatientDeviceKeyStatus.ACTIVE.value,
                    PatientDeviceKey.revoked_at.is_(None),
                )
                .with_for_update()
            )
            if old is None:
                if await _historical_device_exists(
                    db, patient_id=patient_id, device_id=device_id
                ):
                    raise PatientDeviceTrustError("DEVICE_NOT_ACTIVE")
                raise PatientDeviceTrustError("DEVICE_NOT_FOUND")
            if old.key_version != expected_key_version:
                raise PatientDeviceTrustError("DEVICE_KEY_VERSION_STALE")
            if not verify_device_rotation_signature(
                public_key_der=old.device_public_key,
                signing_payload=signing_payload,
                signature_b64=signature_b64,
            ):
                raise PatientDeviceTrustError("DEVICE_ROTATION_SIGNATURE_INVALID")

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

            old_key_id = old.id
            old_key_version = old.key_version
            old.status = PatientDeviceKeyStatus.REPLACED.value
            old.revoked_at = now
            old.revocation_reason_code = "KEY_ROTATED"
            old.revocation_actor = "patient_current_device"
            # Flush the terminal transition first so the partial unique index
            # permits the next active version without ever exposing two active
            # versions outside this transaction.
            await db.flush()

            new = PatientDeviceKey(
                id=new_key_id,
                patient_id=patient_id,
                device_id=device_id,
                key_version=old_key_version + 1,
                device_public_key=canonical.der,
                public_key_fingerprint=canonical.fingerprint,
                device_label=old.device_label,
                platform=old.platform,
                key_algorithm="ECDSA-P256",
                status=PatientDeviceKeyStatus.ACTIVE.value,
                enrolled_at=now,
                revoked_at=None,
                replaces_key_id=old_key_id,
            )
            db.add(new)
            await db.flush()
            old.replaced_by_key_id = new.id
            await db.flush()

            await enqueue_audit_event(
                db,
                audit_context=current_audit_context(AuditDomain.PLATFORM),
                idempotency_key=_audit_key("rotation-replaced", old.id),
                actor_id=actor_id,
                event_type="DEVICE_KEY_REVOKED",
                target_id=str(device_id),
                patient_id=str(patient_id),
                status="SUCCESS",
                metadata={
                    "operation": "device_key_rotation",
                    "key_version": old_key_version,
                    "reason_code": "KEY_ROTATED",
                },
            )
            await enqueue_audit_event(
                db,
                audit_context=current_audit_context(AuditDomain.PLATFORM),
                idempotency_key=_audit_key("rotation-enrolled", new.id),
                actor_id=actor_id,
                event_type="DEVICE_KEY_ENROLLED",
                target_id=str(device_id),
                patient_id=str(patient_id),
                status="SUCCESS",
                metadata={
                    "operation": "device_key_rotation",
                    "key_version": new.key_version,
                    "replaces_key_version": old_key_version,
                },
            )

        return PatientDeviceRotationResult(
            device_id=device_id,
            old_key_id=old_key_id,
            new_key_id=new_key_id,
            old_key_version=old_key_version,
            new_key_version=old_key_version + 1,
            new_public_key_fingerprint=canonical.fingerprint,
            rotated_at=now,
            status=PatientDeviceKeyStatus.ACTIVE.value,
        )
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
            if not await _historical_device_exists(
                db, patient_id=patient_id, device_id=device_id
            ):
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
