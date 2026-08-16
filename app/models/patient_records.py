"""SQLAlchemy models for structured patient clinical records and timeline events (Workstream 3).

Enforces Invariant 3: every clinical observation carries mandatory provenance
columns (`source`, `confidence`, `risk_level`, `source_document_id`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PatientRecord(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Top-level anchor linking structured clinical entities to a patient account."""

    __tablename__ = "patient_records"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, unique=True, index=True
    )


class Vitals(Base, UUIDPrimaryKeyMixin):
    """Quantitative vital observations (e.g. blood pressure, heart rate)."""

    __tablename__ = "patient_vitals"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(
        String(32), nullable=False
    )  # BP, sugar, HR, temp, SpO2
    value: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Provenance columns (Invariant 3)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="LOW_RISK"
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )

    __table_args__ = (
        Index("ix_patient_vitals_patient_recorded", "patient_id", "recorded_at"),
        CheckConstraint(
            "source != 'ai_extracted' OR (confidence IS NOT NULL AND risk_level IS NOT NULL AND source_document_id IS NOT NULL)",
            name="ck_patient_vitals_provenance_complete",
        ),
        ForeignKeyConstraint(
            ["source_document_id", "patient_id"],
            ["document_storage.id", "document_storage.patient_id"],
            name="fk_patient_vitals_source_patient",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_patient_vitals_human_source_fact",
            "patient_id",
            "source_document_id",
            "type",
            "recorded_at",
            unique=True,
            postgresql_where=(
                (source == "human_adjudicated") & source_document_id.is_not(None)
            ),
        ),
    )


class Medication(Base, UUIDPrimaryKeyMixin):
    """Active or historical pharmaceutical prescriptions."""

    __tablename__ = "patient_medications"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    strength: Mapped[str] = mapped_column(String(64), nullable=False)
    frequency: Mapped[str] = mapped_column(String(64), nullable=False)
    prescribed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Provenance columns (Invariant 3)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="MEDIUM_RISK"
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )

    __table_args__ = (
        Index("ix_patient_medications_patient_id", "patient_id"),
        CheckConstraint(
            "source != 'ai_extracted' OR (confidence IS NOT NULL AND risk_level IS NOT NULL AND source_document_id IS NOT NULL)",
            name="ck_patient_medications_provenance_complete",
        ),
    )


class LabResult(Base, UUIDPrimaryKeyMixin):
    """Quantitative and qualitative diagnostic laboratory evaluations."""

    __tablename__ = "patient_lab_results"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    test_name: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[str] = mapped_column(String(64), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_range: Mapped[str] = mapped_column(String(64), nullable=False)
    is_abnormal: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # Provenance columns (Invariant 3)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="MEDIUM_RISK"
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )

    __table_args__ = (
        Index("ix_patient_lab_results_patient_recorded", "patient_id", "recorded_at"),
        CheckConstraint(
            "source != 'ai_extracted' OR (confidence IS NOT NULL AND risk_level IS NOT NULL AND source_document_id IS NOT NULL)",
            name="ck_patient_lab_results_provenance_complete",
        ),
        ForeignKeyConstraint(
            ["source_document_id", "patient_id"],
            ["document_storage.id", "document_storage.patient_id"],
            name="fk_patient_lab_results_source_patient",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_patient_lab_results_human_source_fact",
            "patient_id",
            "source_document_id",
            "test_name",
            "recorded_at",
            unique=True,
            postgresql_where=(
                (source == "human_adjudicated") & source_document_id.is_not(None)
            ),
        ),
    )


class Allergy(Base, UUIDPrimaryKeyMixin):
    """Immunological sensitivities. Enforces HIGH_RISK default per WS5 rules."""

    __tablename__ = "patient_allergies"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    allergen: Mapped[str] = mapped_column(String(128), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)

    # Provenance columns (Invariant 3)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str] = mapped_column(
        String(20), nullable=False, default="HIGH_RISK"
    )
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("risk_level", "HIGH_RISK")
        kwargs.setdefault("source", "manual")
        super().__init__(**kwargs)

    __table_args__ = (
        Index("ix_patient_allergies_patient_id", "patient_id"),
        CheckConstraint(
            "source != 'ai_extracted' OR (confidence IS NOT NULL AND risk_level IS NOT NULL AND source_document_id IS NOT NULL)",
            name="ck_patient_allergies_provenance_complete",
        ),
    )


class DocumentReference(Base, UUIDPrimaryKeyMixin):
    """Records uploaded clinical files and links them to WS4 extraction jobs."""

    __tablename__ = "document_references"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    storage_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    extraction_job_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )


class TimelineEvent(Base, UUIDPrimaryKeyMixin):
    """Unified chronological timeline combining encounters, lab commits, and document ingestion."""

    __tablename__ = "timeline_events"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    event_ref_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    summary: Mapped[str] = mapped_column(String(512), nullable=False)

    __table_args__ = (
        Index("ix_timeline_events_patient_occurred", "patient_id", "occurred_at"),
        Index(
            "uq_timeline_events_human_reference",
            "event_type",
            "event_ref_id",
            unique=True,
            postgresql_where=(
                (source == "human_adjudicated") & event_ref_id.is_not(None)
            ),
        ),
    )
