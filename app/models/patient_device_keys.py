"""SQLAlchemy model for patient cryptographic device trust."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PatientDeviceKeyStatus(str, Enum):
    """Server-owned device-key lifecycle states."""

    ACTIVE = "active"
    REVOKED = "revoked"
    REPLACED = "replaced"
    COMPROMISED = "compromised"


def _fingerprint_default(context) -> str:
    """Compatibility default for trusted ORM fixtures/internal construction.

    Production enrollment canonicalizes the P-256 SubjectPublicKeyInfo DER at
    the service boundary before constructing this model. This default keeps
    trusted direct ORM construction deterministic while the DB global unique
    fingerprint remains the ultimate ownership guard.
    """

    raw = context.get_current_parameters().get("device_public_key")
    if not isinstance(raw, (bytes, bytearray)):
        raise ValueError("device_public_key is required for fingerprinting")
    return hashlib.sha256(bytes(raw)).hexdigest()


class PatientDeviceKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One immutable public-key version within a logical patient device.

    ``device_id`` is the stable server-owned logical device identity. ``id`` is
    the immutable key-version row identity. Only public keys are stored
    server-side; patient private keys are never stored here or elsewhere on the
    backend.
    """

    __tablename__ = "patient_device_keys"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    device_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, default=uuid.uuid4
    )
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    device_public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    public_key_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, default=_fingerprint_default
    )
    device_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    platform: Mapped[str] = mapped_column(String(20), nullable=False)
    key_algorithm: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ECDSA-P256"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PatientDeviceKeyStatus.ACTIVE.value
    )
    enrolled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revocation_reason_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revocation_actor: Mapped[str | None] = mapped_column(String(32), nullable=True)
    replaces_key_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patient_device_keys.id", ondelete="RESTRICT"),
        nullable=True,
    )
    replaced_by_key_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("patient_device_keys.id", ondelete="RESTRICT"),
        nullable=True,
    )

    __table_args__ = (
        # Retain the historical patient/raw-key constraint for compatibility;
        # the fingerprint constraint below is the stronger global invariant.
        UniqueConstraint(
            "patient_id", "device_public_key", name="uq_patient_device_public_key"
        ),
        CheckConstraint(
            "key_version >= 1", name="ck_patient_device_key_version_positive"
        ),
        CheckConstraint(
            "status IN ('active', 'revoked', 'replaced', 'compromised')",
            name="ck_patient_device_key_status",
        ),
        CheckConstraint(
            "(status = 'active' AND revoked_at IS NULL) OR "
            "(status IN ('revoked', 'replaced', 'compromised') AND revoked_at IS NOT NULL)",
            name="ck_patient_device_key_terminal_revoked_at",
        ),
        # Portable metadata-level shape check. The Alembic migration installs
        # the stricter PostgreSQL hexadecimal regex constraint.
        CheckConstraint(
            "length(public_key_fingerprint) = 64 AND "
            "public_key_fingerprint = lower(public_key_fingerprint)",
            name="ck_patient_device_key_fingerprint_format",
        ),
        CheckConstraint(
            "(replaces_key_id IS NULL OR replaces_key_id <> id) AND "
            "(replaced_by_key_id IS NULL OR replaced_by_key_id <> id)",
            name="ck_patient_device_key_lineage_not_self",
        ),
        Index(
            "uq_patient_device_key_fingerprint_global",
            "public_key_fingerprint",
            unique=True,
        ),
        Index(
            "uq_patient_device_key_device_version",
            "device_id",
            "key_version",
            unique=True,
        ),
        Index(
            "uq_patient_device_key_one_active_version",
            "device_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_patient_device_key_patient_status", "patient_id", "status"
        ),
    )
