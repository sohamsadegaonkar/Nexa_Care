"""ORM model for the authoritative erasure registry.

patient_erasure_tombstones is the single source of truth for whether a
patient's data has been erased. It intentionally stores no patient name,
phone, national identifier, or clinical values -- only a patient reference
(id or a non-PII stable hash) and erasure-process state.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class ErasureAssurance(str, Enum):
    """Truthful assurance levels -- see Defect 7. `destroy_dek()` no longer
    claims backup-irrecoverability it cannot prove."""

    ACTIVE_ACCESS_BLOCKED = "active_access_blocked"
    PATIENT_KEY_DELETION_SCHEDULED = "patient_key_deletion_scheduled"
    PATIENT_KEY_DESTROYED = "patient_key_destroyed"


class ErasureStatus(str, Enum):
    REQUESTED = "requested"
    ACCESS_BLOCKED = "access_blocked"
    KEY_DISABLED = "key_disabled"
    DELETION_SCHEDULED = "deletion_scheduled"
    DESTROYED = "destroyed"
    OPERATOR_ACTION_REQUIRED = "operator_action_required"


class WrappingKeyType(str, Enum):
    SHARED = "shared"
    PATIENT = "patient"


class PatientErasureTombstone(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "patient_erasure_tombstones"

    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    patient_ref: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=ErasureStatus.REQUESTED.value)
    assurance_level: Mapped[str] = mapped_column(String(64), nullable=False)
    wrapping_key_type: Mapped[str] = mapped_column(String(16), nullable=False)
    patient_wrapping_key_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kms_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    effective_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_deletion_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completion_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operator_action_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    retry_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    audit_event_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())