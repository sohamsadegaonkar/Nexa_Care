"""Patient-owned external medical record import persistence.

This domain is deliberately separate from provider delegated document-processing
authority.  Rows are owned directly by the authenticated patient identity and
must never be interpreted as clinician-created or clinician-verified records.
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
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


PATIENT_EXTERNAL_RECORD_CATEGORIES = (
    "PRESCRIPTION",
    "LAB_REPORT",
    "IMAGING_REPORT",
    "DISCHARGE_SUMMARY",
    "OTHER_MEDICAL_RECORD",
)

PATIENT_EXTERNAL_RECORD_IMPORT_STATUSES = (
    "UPLOADED",
    "PROCESSING",
    "REVIEW_REQUIRED",
    "READY_TO_SAVE",
    "COMPLETED",
    "FAILED_RETRYABLE",
    "FAILED_TERMINAL",
    "CANCELLED",
)

PATIENT_EXTERNAL_RECORD_REVIEW_STATUSES = (
    "NEEDS_REVIEW",
    "ACCEPTED",
    "CORRECTED",
    "REJECTED",
)


class PatientExternalRecordImport(Base, UUIDPrimaryKeyMixin):
    """Patient-owned orchestration state for one retained external source.

    The row contains workflow metadata and references only.  Extracted medical
    values live in encrypted evidence rows and accepted facts are ultimately
    written to the existing typed record models.
    """

    __tablename__ = "patient_external_record_imports"

    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="UPLOADED")
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extractor_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    final_record_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    final_record_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    timeline_event_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["patient_id"],
            ["patients.patient_uuid"],
            name="fk_patient_external_record_import_patient",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_document_id", "patient_id"],
            ["document_storage.id", "document_storage.patient_id"],
            name="fk_patient_external_record_import_source_owner",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "source_document_id",
            "patient_id",
            name="uq_patient_external_record_import_source_owner",
        ),
        UniqueConstraint(
            "id",
            "patient_id",
            "source_document_id",
            name="uq_patient_external_record_import_graph",
        ),
        CheckConstraint(
            "category IN ('PRESCRIPTION','LAB_REPORT','IMAGING_REPORT',"
            "'DISCHARGE_SUMMARY','OTHER_MEDICAL_RECORD')",
            name="ck_patient_external_record_import_category",
        ),
        CheckConstraint(
            "status IN ('UPLOADED','PROCESSING','REVIEW_REQUIRED','READY_TO_SAVE',"
            "'COMPLETED','FAILED_RETRYABLE','FAILED_TERMINAL','CANCELLED')",
            name="ck_patient_external_record_import_status",
        ),
        CheckConstraint(
            "(status = 'FAILED_RETRYABLE' AND retryable) OR "
            "(status <> 'FAILED_RETRYABLE')",
            name="ck_patient_external_record_import_retryable_status",
        ),
        CheckConstraint(
            "(status = 'COMPLETED' AND final_record_type IS NOT NULL "
            "AND final_record_id IS NOT NULL AND timeline_event_id IS NOT NULL "
            "AND completed_at IS NOT NULL) OR status <> 'COMPLETED'",
            name="ck_patient_external_record_import_completion_refs",
        ),
        Index(
            "ix_patient_external_record_import_patient_status",
            "patient_id",
            "status",
            "created_at",
        ),
    )


class PatientExternalRecordCandidate(Base, UUIDPrimaryKeyMixin):
    """Encrypted field evidence extracted from a patient-owned source document."""

    __tablename__ = "patient_external_record_candidates"

    import_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, unique=True
    )
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    clinical_fact_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    encrypted_raw_value: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    encrypted_reviewed_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_page: Mapped[int | None] = mapped_column(nullable=True)
    source_bbox_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    field_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    document_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    extractor_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    review_status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="NEEDS_REVIEW"
    )
    patient_reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extracted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["import_id", "patient_id", "source_document_id"],
            [
                "patient_external_record_imports.id",
                "patient_external_record_imports.patient_id",
                "patient_external_record_imports.source_document_id",
            ],
            name="fk_patient_external_record_candidate_import_graph",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "review_status IN ('NEEDS_REVIEW','ACCEPTED','CORRECTED','REJECTED')",
            name="ck_patient_external_record_candidate_review_status",
        ),
        CheckConstraint(
            "field_confidence IS NULL OR "
            "(field_confidence >= 0.0 AND field_confidence <= 1.0)",
            name="ck_patient_external_record_candidate_field_confidence",
        ),
        CheckConstraint(
            "document_confidence IS NULL OR "
            "(document_confidence >= 0.0 AND document_confidence <= 1.0)",
            name="ck_patient_external_record_candidate_document_confidence",
        ),
        CheckConstraint(
            "(review_status = 'CORRECTED' AND patient_reviewed "
            "AND encrypted_reviewed_value IS NOT NULL AND reviewed_at IS NOT NULL) OR "
            "review_status <> 'CORRECTED'",
            name="ck_patient_external_record_candidate_correction_provenance",
        ),
        Index(
            "ix_patient_external_record_candidate_import_review",
            "import_id",
            "review_status",
        ),
    )
