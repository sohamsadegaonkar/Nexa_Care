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

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class DocumentStorage(Base, UUIDPrimaryKeyMixin):
    """Stores upload metadata for raw medical documents before AI extraction."""

    __tablename__ = "document_storage"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    uploader_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    storage_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    upload_purpose: Mapped[str | None] = mapped_column(String(64), nullable=True)
    consent_session_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_system: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "id",
            "patient_id",
            name="uq_document_storage_id_patient",
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "patient_id",
            name="uq_document_storage_id_tenant_patient",
        ),
        UniqueConstraint(
            "tenant_id",
            "patient_id",
            "content_hash",
            name="uq_document_tenant_patient_hash",
        ),
        Index("ix_document_storage_hash", "content_hash"),
    )


class ExtractionJob(Base, UUIDPrimaryKeyMixin):
    """Tracks the end-to-end lifecycle of an asynchronous AI extraction task."""

    __tablename__ = "extraction_jobs"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    uploader_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    authorization_provider_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    consent_request_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_storage.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    request_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extractor_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "id",
            "tenant_id",
            "patient_id",
            "document_id",
            name="uq_extraction_jobs_authoritative_graph",
        ),
        ForeignKeyConstraint(
            ["document_id", "tenant_id", "patient_id"],
            [
                "document_storage.id",
                "document_storage.tenant_id",
                "document_storage.patient_id",
            ],
            name="fk_extraction_jobs_authoritative_document_graph",
            ondelete="CASCADE",
        ),
        Index(
            "ix_extraction_jobs_authorization_binding",
            "patient_id",
            "tenant_id",
            "authorization_provider_id",
            "consent_request_id",
        ),
    )


class ExtractedFieldRecord(Base, UUIDPrimaryKeyMixin):
    """Persistent storage for canonical ExtractedField schema (WS1 single source of truth).

    Strictly mirrors the ExtractedField Pydantic model with slots for confidence,
    risk_level, validation_result, source_page, and source_bbox.
    """

    __tablename__ = "extracted_fields"

    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patient_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    raw_value: Mapped[str] = mapped_column(String(512), nullable=False)
    normalized_value: Mapped[str | None] = mapped_column(String(512), nullable=True)
    units: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(32), nullable=False)
    validation_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_bbox: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="needs_review"
    )
    corrected_value: Mapped[str | None] = mapped_column(String(512), nullable=True)
    review_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    extractor_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    extractor_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    committed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (Index("ix_extracted_fields_job_status", "job_id", "status"),)


class ExtractionCandidateRecord(Base, UUIDPrimaryKeyMixin):
    """Encrypted, authorization-bound provider candidates for truthful result display.

    Rows share the source document/job lifecycle and are never clinical authority.
    """

    __tablename__ = "extraction_candidates"

    evidence_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, unique=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_storage.id", ondelete="CASCADE"),
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    authorization_provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    clinical_fact_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    encrypted_raw_value: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_bbox: Mapped[list[float] | None] = mapped_column(JSONB, nullable=True)
    field_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    document_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_name: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_version: Mapped[str] = mapped_column(String(64), nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    evidence_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    lane: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    routing_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    eligibility_reason_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    eligibility_policy_version: Mapped[str] = mapped_column(
        String(16), nullable=False, default="v1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "lane IN ('SOURCE_ONLY', 'QUARANTINE')",
            name="ck_extraction_candidates_safe_lane",
        ),
        CheckConstraint(
            "(routing_eligible AND eligibility_reason_code IS NULL) OR "
            "(NOT routing_eligible AND eligibility_reason_code IS NOT NULL AND "
            "eligibility_reason_code IN ("
            "'INELIGIBLE_QUERY_ONLY_INVALID_FORMAT', "
            "'INELIGIBLE_CLASSIFICATION_FAILED')",
            name="ck_extraction_candidates_eligibility",
        ),
        UniqueConstraint(
            "id", "evidence_id", name="uq_extraction_candidates_id_evidence"
        ),
        ForeignKeyConstraint(
            ["job_id", "tenant_id", "patient_id", "source_document_id"],
            [
                "extraction_jobs.id",
                "extraction_jobs.tenant_id",
                "extraction_jobs.patient_id",
                "extraction_jobs.document_id",
            ],
            name="fk_extraction_candidates_authoritative_job_graph",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["source_document_id", "tenant_id", "patient_id"],
            [
                "document_storage.id",
                "document_storage.tenant_id",
                "document_storage.patient_id",
            ],
            name="fk_extraction_candidates_authoritative_document_graph",
            ondelete="CASCADE",
        ),
        Index(
            "ix_extraction_candidates_authorization_binding",
            "tenant_id",
            "patient_id",
            "authorization_provider_id",
            "job_id",
        ),
        Index("ix_extraction_candidates_document", "source_document_id"),
        Index("ix_extraction_candidates_clinical_fact", "clinical_fact_key"),
        Index(
            "ix_extraction_candidates_job_routing_eligible",
            "job_id",
            "routing_eligible",
        ),
    )


class ExtractionAttemptEventRecord(Base):
    """Append-only, value-free provenance for one provider subattempt."""

    __tablename__ = "extraction_attempt_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, nullable=False
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_storage.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_subattempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_adapter: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_model_version: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "job_attempt_number >= 1",
            name="ck_extraction_attempt_events_job_attempt_positive",
        ),
        CheckConstraint(
            "provider_subattempt_number >= 1",
            name="ck_extraction_attempt_events_provider_subattempt_positive",
        ),
        CheckConstraint(
            "outcome IN ('SUCCEEDED', 'TIMEOUT', 'THROTTLED', "
            "'RETRYABLE_ERROR', 'INVALID_DOCUMENT', 'INVALID_RESPONSE', "
            "'CREDENTIALS_UNAVAILABLE')",
            name="ck_extraction_attempt_events_outcome",
        ),
        CheckConstraint(
            "(outcome = 'SUCCEEDED' AND response_complete = true "
            "AND error_code IS NULL) OR "
            "(outcome <> 'SUCCEEDED' AND response_complete = false)",
            name="ck_extraction_attempt_events_completion",
        ),
        UniqueConstraint(
            "job_id",
            "job_attempt_number",
            "provider_subattempt_number",
            name="uq_extraction_attempt_events_logical_identity",
        ),
        Index(
            "ix_extraction_attempt_events_job_attempt",
            "job_id",
            "job_attempt_number",
        ),
        Index(
            "ix_extraction_attempt_events_tenant_patient_job",
            "tenant_id",
            "patient_id",
            "job_id",
        ),
    )


class ExtractionFailureQuarantineRecord(Base, UUIDPrimaryKeyMixin):
    """Operational lifecycle for a retry-exhausted job with no extraction result.

    This intentionally stores no provider payload, OCR, or clinical evidence.  It
    is a lifecycle child of the authoritative job graph, not an extraction
    decision or routing projection.
    """

    __tablename__ = "extraction_failure_quarantines"

    job_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    review_deadline: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disposition: Mapped[str | None] = mapped_column(String(48), nullable=True)
    disposed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disposed_by_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    disposition_idempotency_key: Mapped[str | None] = mapped_column(
        String(192), nullable=True
    )
    disposition_request_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("job_id", name="uq_extraction_failure_quarantines_job"),
        ForeignKeyConstraint(
            ["job_id", "tenant_id", "patient_id", "source_document_id"],
            [
                "extraction_jobs.id",
                "extraction_jobs.tenant_id",
                "extraction_jobs.patient_id",
                "extraction_jobs.document_id",
            ],
            name="fk_failure_quarantines_authoritative_job_graph",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "reason_code = 'PROVIDER_RETRY_EXHAUSTED'",
            name="ck_failure_quarantines_reason_code",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'ESCALATED', 'DISPOSED')",
            name="ck_failure_quarantines_status",
        ),
        CheckConstraint("version >= 1", name="ck_failure_quarantines_version"),
        CheckConstraint(
            "(status = 'PENDING' AND escalated_at IS NULL AND disposition IS NULL "
            "AND disposed_at IS NULL AND disposed_by_provider_id IS NULL) OR "
            "(status = 'ESCALATED' AND escalated_at IS NOT NULL AND disposition IS NULL "
            "AND disposed_at IS NULL AND disposed_by_provider_id IS NULL) OR "
            "(status = 'DISPOSED' AND escalated_at IS NOT NULL AND disposition IN "
            "('RETAIN_SOURCE_NO_CLINICAL_COMMIT', 'REJECT_PROCESSING_RETAIN_AUDIT') "
            "AND disposed_at IS NOT NULL AND disposed_by_provider_id IS NOT NULL)",
            name="ck_failure_quarantines_lifecycle",
        ),
        CheckConstraint(
            "(disposition_idempotency_key IS NULL AND disposition_request_hash IS NULL) "
            "OR (disposition_idempotency_key IS NOT NULL AND disposition_request_hash IS NOT NULL)",
            name="ck_failure_quarantines_idempotency_pair",
        ),
        Index(
            "ix_failure_quarantines_processor",
            "status",
            "review_deadline",
            "id",
        ),
    )


class ExtractionConflictRecord(Base, UUIDPrimaryKeyMixin):
    """Append-only, non-authoritative set of incompatible candidate facts."""

    __tablename__ = "extraction_conflicts"

    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_storage.id", ondelete="RESTRICT"),
        nullable=False,
    )
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    clinical_fact_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "char_length(clinical_fact_key) = 64",
            name="ck_extraction_conflicts_fact_key_length",
        ),
        UniqueConstraint(
            "job_id", "clinical_fact_key", name="uq_extraction_conflicts_job_fact"
        ),
        UniqueConstraint(
            "id",
            "tenant_id",
            "patient_id",
            "job_id",
            "source_document_id",
            name="uq_extraction_conflicts_authoritative_graph",
        ),
        Index("ix_extraction_conflicts_case_graph", "job_id", "source_document_id"),
    )


class ExtractionConflictMemberRecord(Base, UUIDPrimaryKeyMixin):
    """Immutable membership linking every original candidate to its conflict."""

    __tablename__ = "extraction_conflict_members"

    conflict_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_conflicts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["candidate_id", "evidence_id"],
            ["extraction_candidates.id", "extraction_candidates.evidence_id"],
            name="fk_conflict_member_candidate_evidence",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "conflict_id", "candidate_id", name="uq_conflict_member_candidate"
        ),
        UniqueConstraint(
            "conflict_id", "evidence_id", name="uq_conflict_member_evidence"
        ),
        Index("ix_extraction_conflict_members_conflict", "conflict_id"),
    )


class DocumentSourceRelationshipRecord(Base, UUIDPrimaryKeyMixin):
    """Append-only provenance edge from a newer source to an earlier source."""

    __tablename__ = "document_source_relationships"

    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_storage.id", ondelete="RESTRICT"),
        nullable=False,
    )
    related_document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_storage.id", ondelete="RESTRICT"),
        nullable=False,
    )
    relation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "relation_type IN ('SUPERSEDES', 'ADDENDUM_TO')",
            name="ck_document_source_relationships_type",
        ),
        CheckConstraint(
            "source_document_id <> related_document_id",
            name="ck_document_source_relationships_not_self",
        ),
        UniqueConstraint(
            "source_document_id", name="uq_document_source_relationships_source"
        ),
        Index("ix_document_source_relationships_related", "related_document_id"),
    )


class PipelineCommit(Base, UUIDPrimaryKeyMixin):
    """Job-level commit transaction marker tracking ingestion into clinical sub-models."""

    __tablename__ = "pipeline_commits"

    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, unique=True, index=True
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    committed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    committed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ingested_count: Mapped[int] = mapped_column(Integer, nullable=False)


class ReviewQueueItem(Base, UUIDPrimaryKeyMixin):
    """Links an ExtractedFieldRecord with status=needs_review to human adjudication queue."""

    __tablename__ = "review_queue_items"

    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    field_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extracted_fields.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    adjudicated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    adjudicated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)

    __table_args__ = (
        Index("ix_review_queue_items_patient_status", "patient_id", "status"),
    )


class FieldCorrection(Base, UUIDPrimaryKeyMixin):
    """Logs human steward corrections on extracted observations for WS5 evaluation datasets."""

    __tablename__ = "field_corrections"

    field_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extracted_fields.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(128), nullable=False)
    original_value: Mapped[str] = mapped_column(String(512), nullable=False)
    corrected_value: Mapped[str] = mapped_column(String(512), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    corrected_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    corrected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class ExtractionDecisionRecord(Base, UUIDPrimaryKeyMixin):
    """Append-only safe projection of an extraction lane decision."""

    __tablename__ = "extraction_decisions"

    decision_contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_storage.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workflow_id: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(96), nullable=False)
    lane: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    evaluator_version: Mapped[str] = mapped_column(String(64), nullable=False)
    auto_commit_feature_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    earlier_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_decisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "lane IN ('SOURCE_ONLY', 'QUARANTINE')",
            name="ck_extraction_decisions_safe_lane",
        ),
        CheckConstraint(
            "auto_commit_feature_enabled = false",
            name="ck_extraction_decisions_auto_commit_disabled",
        ),
        CheckConstraint(
            "organization_id = tenant_id",
            name="ck_extraction_decisions_organization_tenant",
        ),
        Index("ix_extraction_decisions_tenant_patient", "tenant_id", "patient_id"),
        Index("ix_extraction_decisions_job_lane", "job_id", "lane"),
        Index("ix_extraction_decisions_evidence", "evidence_id"),
        UniqueConstraint(
            "id",
            "job_id",
            "tenant_id",
            "patient_id",
            "source_document_id",
            name="uq_extraction_decisions_authoritative_graph",
        ),
        ForeignKeyConstraint(
            ["job_id", "tenant_id", "patient_id", "source_document_id"],
            [
                "extraction_jobs.id",
                "extraction_jobs.tenant_id",
                "extraction_jobs.patient_id",
                "extraction_jobs.document_id",
            ],
            name="fk_extraction_decisions_authoritative_job_graph",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_document_id", "tenant_id", "patient_id"],
            [
                "document_storage.id",
                "document_storage.tenant_id",
                "document_storage.patient_id",
            ],
            name="fk_extraction_decisions_authoritative_document_graph",
            ondelete="RESTRICT",
        ),
    )


class ExtractionRoutingRecord(Base, UUIDPrimaryKeyMixin):
    """Mutable operational routing state separated from immutable decisions."""

    __tablename__ = "extraction_routing"

    decision_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_decisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_storage.id", ondelete="RESTRICT"),
        nullable=False,
    )
    lane: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    routed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    quarantine_review_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolution_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(192), nullable=False)
    operation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "lane IN ('SOURCE_ONLY', 'QUARANTINE')",
            name="ck_extraction_routing_safe_lane",
        ),
        CheckConstraint(
            "status IN ('SOURCE_RETAINED', 'QUARANTINE_PENDING', "
            "'QUARANTINE_ESCALATED')",
            name="ck_extraction_routing_status",
        ),
        CheckConstraint(
            "(lane = 'SOURCE_ONLY' AND status = 'SOURCE_RETAINED' "
            "AND quarantine_review_deadline IS NULL) OR "
            "(lane = 'QUARANTINE' AND status IN "
            "('QUARANTINE_PENDING', 'QUARANTINE_ESCALATED') "
            "AND quarantine_review_deadline IS NOT NULL)",
            name="ck_extraction_routing_lane_state",
        ),
        CheckConstraint(
            "(status = 'QUARANTINE_ESCALATED' AND escalated_at IS NOT NULL) OR "
            "(status <> 'QUARANTINE_ESCALATED' AND escalated_at IS NULL)",
            name="ck_extraction_routing_escalation_time",
        ),
        UniqueConstraint("decision_id", name="uq_extraction_routing_decision"),
        UniqueConstraint("idempotency_key", name="uq_extraction_routing_idempotency"),
        ForeignKeyConstraint(
            [
                "decision_id",
                "job_id",
                "tenant_id",
                "patient_id",
                "source_document_id",
            ],
            [
                "extraction_decisions.id",
                "extraction_decisions.job_id",
                "extraction_decisions.tenant_id",
                "extraction_decisions.patient_id",
                "extraction_decisions.source_document_id",
            ],
            name="fk_extraction_routing_authoritative_decision_graph",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["job_id", "tenant_id", "patient_id", "source_document_id"],
            [
                "extraction_jobs.id",
                "extraction_jobs.tenant_id",
                "extraction_jobs.patient_id",
                "extraction_jobs.document_id",
            ],
            name="fk_extraction_routing_authoritative_job_graph",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["source_document_id", "tenant_id", "patient_id"],
            [
                "document_storage.id",
                "document_storage.tenant_id",
                "document_storage.patient_id",
            ],
            name="fk_extraction_routing_authoritative_document_graph",
            ondelete="RESTRICT",
        ),
        Index("ix_extraction_routing_tenant_patient", "tenant_id", "patient_id"),
        Index("ix_extraction_routing_job_lane", "job_id", "lane"),
        Index(
            "ix_extraction_routing_unresolved_quarantine",
            "status",
            "quarantine_review_deadline",
        ),
    )


class AdjudicationCaseRecord(Base, UUIDPrimaryKeyMixin):
    """Mutable workflow state for one authorized archived-source review."""

    __tablename__ = "adjudication_cases"

    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_storage.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    routing_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_routing.id", ondelete="RESTRICT"),
        nullable=True,
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_decisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reviewer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer_organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    reviewer_role: Mapped[str] = mapped_column(String(32), nullable=False)
    review_session_id: Mapped[str] = mapped_column(String(96), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    accepted_submission_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
    )
    clinical_committed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(192), nullable=False)
    operation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'REJECTED', "
            "'NEEDS_SPECIALIST_REVIEW')",
            name="ck_adjudication_cases_status",
        ),
        CheckConstraint(
            "(routing_id IS NULL AND decision_id IS NULL) OR "
            "(routing_id IS NOT NULL AND decision_id IS NOT NULL)",
            name="ck_adjudication_cases_source_binding",
        ),
        CheckConstraint("version > 0", name="ck_adjudication_cases_version_positive"),
        CheckConstraint(
            "char_length(operation_hash) = 64",
            name="ck_adjudication_cases_operation_hash_length",
        ),
        ForeignKeyConstraint(
            ["id", "accepted_submission_id"],
            ["adjudication_submissions.case_id", "adjudication_submissions.id"],
            name="fk_adjudication_cases_accepted_submission_same_case",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        UniqueConstraint("idempotency_key", name="uq_adjudication_cases_idempotency"),
        Index("ix_adjudication_cases_tenant_status", "tenant_id", "status"),
        Index("ix_adjudication_cases_reviewer_status", "reviewer_id", "status"),
        Index("ix_adjudication_cases_patient", "patient_id"),
        Index("ix_adjudication_cases_job", "job_id"),
    )


class AdjudicationSubmissionRecord(Base, UUIDPrimaryKeyMixin):
    """Immutable protected reviewer submission."""

    __tablename__ = "adjudication_submissions"

    case_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("adjudication_cases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_document_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("document_storage.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    routing_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_routing.id", ondelete="RESTRICT"),
        nullable=True,
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_decisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    reviewer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewer_organization_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    reviewer_role: Mapped[str] = mapped_column(String(32), nullable=False)
    review_session_id: Mapped[str] = mapped_column(String(96), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    clinical_payload: Mapped[list[dict]] = mapped_column(JSONB, nullable=False)
    resolved_conflict_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    supersedes_submission_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("adjudication_submissions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    contract_version: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(192), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('ACCEPTED', 'REJECTED', "
            "'NEEDS_SPECIALIST_REVIEW', 'SUPERSEDED')",
            name="ck_adjudication_submissions_outcome",
        ),
        CheckConstraint(
            "(routing_id IS NULL AND decision_id IS NULL) OR "
            "(routing_id IS NOT NULL AND decision_id IS NOT NULL)",
            name="ck_adjudication_submissions_source_binding",
        ),
        CheckConstraint(
            "attempt_number > 0",
            name="ck_adjudication_submissions_attempt_positive",
        ),
        CheckConstraint(
            "char_length(content_hash) = 64",
            name="ck_adjudication_submissions_content_hash_length",
        ),
        UniqueConstraint(
            "idempotency_key", name="uq_adjudication_submissions_idempotency"
        ),
        UniqueConstraint(
            "case_id",
            "attempt_number",
            name="uq_adjudication_submissions_case_attempt",
        ),
        UniqueConstraint(
            "case_id",
            "id",
            name="uq_adjudication_submissions_case_id_id",
        ),
        Index("ix_adjudication_submissions_case", "case_id"),
        Index("ix_adjudication_submissions_reviewer", "reviewer_id"),
    )


class AdjudicationConflictResolutionRecord(Base, UUIDPrimaryKeyMixin):
    """Immutable proof that one protected submission resolved one conflict."""

    __tablename__ = "adjudication_conflict_resolutions"

    submission_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("adjudication_submissions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    conflict_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("extraction_conflicts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    case_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("adjudication_cases.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "submission_id",
            "conflict_id",
            name="uq_adjudication_conflict_resolution",
        ),
        Index(
            "ix_adjudication_conflict_resolutions_case_conflict",
            "case_id",
            "conflict_id",
        ),
    )
