"""SQLAlchemy models for AI Ingestion Pipeline (Workstream 4).

Defines:
- DocumentStorage: metadata for uploaded clinical files.
- ExtractionJob: background extraction job execution lifecycle.
- ExtractedFieldRecord: canonical extracted observation matching WS1 schema.
- ReviewQueueItem: human steward adjudication queue items.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class DocumentStorage(Base, UUIDPrimaryKeyMixin):
    """Stores upload metadata for raw medical documents before AI extraction."""

    __tablename__ = "document_storage"

    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    uploader_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    upload_purpose: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consent_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_system: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "patient_id", "content_hash", name="uq_document_tenant_patient_hash"),
        Index("ix_document_storage_hash", "content_hash"),
    )


class ExtractionJob(Base, UUIDPrimaryKeyMixin):
    """Tracks the end-to-end lifecycle of an asynchronous AI extraction task."""

    __tablename__ = "extraction_jobs"

    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    uploader_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("document_storage.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extractor_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExtractedFieldRecord(Base, UUIDPrimaryKeyMixin):
    """Persistent storage for canonical ExtractedField schema (WS1 single source of truth).

    Strictly mirrors the ExtractedField Pydantic model with slots for confidence,
    risk_level, validation_result, source_page, and source_bbox.
    """

    __tablename__ = "extracted_fields"

    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("extraction_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_value: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_value: Mapped[str | None] = mapped_column(String(512), nullable=True)
    units: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    validation_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_page: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_bbox: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="needs_review")
    corrected_value: Mapped[str | None] = mapped_column(String(512), nullable=True)
    review_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    extractor_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    committed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    committed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        Index("ix_extracted_fields_job_status", "job_id", "status"),
    )


class PipelineCommit(Base, UUIDPrimaryKeyMixin):
    """Job-level commit transaction marker tracking ingestion into clinical sub-models."""

    __tablename__ = "pipeline_commits"

    job_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, unique=True, index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    committed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    committed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ingested_count: Mapped[int] = mapped_column(Integer, nullable=False)


class ReviewQueueItem(Base, UUIDPrimaryKeyMixin):
    """Links an ExtractedFieldRecord with status=needs_review to human adjudication queue."""

    __tablename__ = "review_queue_items"

    job_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    field_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("extracted_fields.id", ondelete="CASCADE"), nullable=False, index=True
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    adjudicated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adjudicated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        Index("ix_review_queue_items_patient_status", "patient_id", "status"),
    )


class FieldCorrection(Base, UUIDPrimaryKeyMixin):
    """Logs human steward corrections on extracted observations for WS5 evaluation datasets."""

    __tablename__ = "field_corrections"

    field_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("extracted_fields.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    original_value: Mapped[str] = mapped_column(String(512), nullable=False)
    corrected_value: Mapped[str] = mapped_column(String(512), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    corrected_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    corrected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
