"""Patient profile service — encrypted B2C profile management.

Transaction ownership: this service stages mutations on the provided
AsyncSession but NEVER commits.  The caller owns BEGIN/COMMIT/ROLLBACK.

Read path (get_profile) is strictly read-only — it NEVER provisions DEKs.
Write path (create_or_update_profile) uses ensure_active_dek() to stage
a DEK if needed, within the caller's transaction.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.patient import Patient
from app.models.patient_profile import PatientProfile
from app.security.audit_context import AuditDomain, current_audit_context
from app.services.audit_outbox import enqueue_audit_event
from app.services.crypto_kms import (
    EncryptedField,
    get_encryption_provider,
)


class ProfileValidationError(ValueError):
    """Raised when profile input fails validation."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(detail)


@dataclass(frozen=True)
class ProfileData:
    """Decrypted patient profile values."""

    full_name: str
    date_of_birth: str  # YYYY-MM-DD


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_NAME_LENGTH = 200


def _validate_full_name(raw: str) -> str:
    """Validate and normalize a full name input."""
    name = raw.strip()
    if not name:
        raise ProfileValidationError(
            "INVALID_FULL_NAME", "Full name must not be empty."
        )
    if len(name) > _MAX_NAME_LENGTH:
        raise ProfileValidationError(
            "INVALID_FULL_NAME",
            f"Full name must not exceed {_MAX_NAME_LENGTH} characters.",
        )
    if _CONTROL_CHAR_RE.search(name):
        raise ProfileValidationError(
            "INVALID_FULL_NAME", "Full name contains invalid control characters."
        )
    return name


def _validate_date_of_birth(dob: date) -> str:
    """Validate date of birth and return canonical YYYY-MM-DD string."""
    if dob > date.today():
        raise ProfileValidationError(
            "INVALID_DATE_OF_BIRTH", "Date of birth must not be in the future."
        )
    return dob.isoformat()  # YYYY-MM-DD


async def create_or_update_profile(
    patient_id: str,
    full_name: str,
    date_of_birth: date,
    db: AsyncSession,
) -> tuple[ProfileData, bool]:
    """Create or update an encrypted patient profile.

    Returns (ProfileData, created) where created=True for first write.
    Stages all mutations on ``db`` WITHOUT committing.
    """
    validated_name = _validate_full_name(full_name)
    validated_dob = _validate_date_of_birth(date_of_birth)
    pid = uuid.UUID(patient_id)
    kms = get_encryption_provider()

    # Lock the authoritative Patient row to serialize concurrent profile writes
    patient_row = (
        await db.execute(
            select(Patient).where(Patient.patient_uuid == pid).with_for_update()
        )
    ).scalar_one_or_none()
    if patient_row is None:
        raise ProfileValidationError("PATIENT_NOT_FOUND", "Patient does not exist.")

    existing = (
        await db.execute(select(PatientProfile).where(PatientProfile.patient_id == pid))
    ).scalar_one_or_none()

    if existing is not None:
        # UPDATE path — decrypt current, compare, update only if changed
        current_name = await kms.decrypt_field(
            patient_id,
            "full_name",
            EncryptedField.deserialize(existing.full_name_encrypted, "full_name"),
            db,
        )
        current_dob = await kms.decrypt_field(
            patient_id,
            "date_of_birth",
            EncryptedField.deserialize(
                existing.date_of_birth_encrypted, "date_of_birth"
            ),
            db,
        )

        if current_name == validated_name and current_dob == validated_dob:
            # Exact no-op — no ciphertext rewrite, no audit event
            return ProfileData(
                full_name=validated_name, date_of_birth=validated_dob
            ), False

        # Values changed — encrypt fresh and update
        name_enc = await kms.encrypt_field(patient_id, "full_name", validated_name, db)
        dob_enc = await kms.encrypt_field(
            patient_id, "date_of_birth", validated_dob, db
        )
        existing.full_name_encrypted = name_enc.serialize()
        existing.date_of_birth_encrypted = dob_enc.serialize()
        existing.updated_at = datetime.now(timezone.utc)

        fields_changed = []
        if current_name != validated_name:
            fields_changed.append("full_name")
        if current_dob != validated_dob:
            fields_changed.append("date_of_birth")

        await enqueue_audit_event(
            db,
            audit_context=current_audit_context(AuditDomain.PATIENT_RECORD),
            idempotency_key=f"profile-update:{patient_id}:{datetime.now(timezone.utc).isoformat()}",
            actor_id=patient_id,
            event_type="PATIENT_PROFILE_UPDATED",
            target_id=patient_id,
            patient_id=patient_id,
            metadata={"fields_changed": fields_changed},
        )
        return ProfileData(full_name=validated_name, date_of_birth=validated_dob), False

    # CREATE path — provision DEK in caller's transaction, encrypt, insert
    await kms.ensure_active_dek(patient_id, db)
    name_enc = await kms.encrypt_field(patient_id, "full_name", validated_name, db)
    dob_enc = await kms.encrypt_field(patient_id, "date_of_birth", validated_dob, db)

    now = datetime.now(timezone.utc)
    profile = PatientProfile(
        patient_id=pid,
        full_name_encrypted=name_enc.serialize(),
        date_of_birth_encrypted=dob_enc.serialize(),
        created_at=now,
        updated_at=now,
    )
    db.add(profile)
    await db.flush()

    await enqueue_audit_event(
        db,
        audit_context=current_audit_context(AuditDomain.PATIENT_RECORD),
        idempotency_key=f"profile-create:{patient_id}",
        actor_id=patient_id,
        event_type="PATIENT_PROFILE_CREATED",
        target_id=patient_id,
        patient_id=patient_id,
    )
    return ProfileData(full_name=validated_name, date_of_birth=validated_dob), True


async def get_profile(patient_id: str, db: AsyncSession) -> ProfileData | None:
    """Read and decrypt the patient's profile.

    This is STRICTLY READ-ONLY — it NEVER provisions DEKs.
    If the profile exists but the DEK is missing/destroyed, the crypto
    provider's decrypt path will raise the appropriate error.
    """
    pid = uuid.UUID(patient_id)
    row = (
        await db.execute(select(PatientProfile).where(PatientProfile.patient_id == pid))
    ).scalar_one_or_none()

    if row is None:
        return None

    kms = get_encryption_provider()
    name = await kms.decrypt_field(
        patient_id,
        "full_name",
        EncryptedField.deserialize(row.full_name_encrypted, "full_name"),
        db,
    )
    dob = await kms.decrypt_field(
        patient_id,
        "date_of_birth",
        EncryptedField.deserialize(row.date_of_birth_encrypted, "date_of_birth"),
        db,
    )
    return ProfileData(full_name=name, date_of_birth=dob)
