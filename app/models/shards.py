"""SQLAlchemy models for Nexa Care vertical shard tables.

These models exist so local/live bootstrap scripts can create the same tables
that older Supabase and raw-SQL code paths already use. The privacy boundary is
kept explicit: identity data lives in ``nexa_vault`` and clinical facts live in
``nexa_clinical``.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class NexaVault(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """PII vault shard keyed by a masked internal patient identifier."""

    __tablename__ = "nexa_vault"

    masked_internal_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    patient_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    aadhaar_abha_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("ix_nexa_vault_masked_internal_id", "masked_internal_id"),)


class NexaClinical(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Clinical shard keyed by the same masked identifier, without PII."""

    __tablename__ = "nexa_clinical"

    masked_internal_id: Mapped[str] = mapped_column(String(64), nullable=False)
    diagnoses: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    lab_results: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    prescriptions: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    clinical_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_nexa_clinical_masked_internal_id", "masked_internal_id"),
    )


class NexaEmergencySnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Read-optimized emergency projection keyed by patient UUID."""

    __tablename__ = "nexa_emergency_snapshot"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, unique=True
    )
    allergies: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    conditions: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    medications: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    emergency_notes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (Index("ix_nexa_emergency_snapshot_patient_id", "patient_id"),)
