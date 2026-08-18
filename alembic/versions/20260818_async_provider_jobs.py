"""Add the durable asynchronous provider-job contract for Scenario 6 A1.

Revision ID: 20260818_async_provider_jobs
Revises: 20260817_failure_quarantine

Purpose: add one value-free operational row for each durable Nexa provider
attempt, including bounded lifecycle, correlation, completeness, reconciliation,
and controlled-supersession metadata.
Preconditions: the database is at ``20260817_failure_quarantine`` and its
authoritative extraction/document graph constraints are present.
Existing-data behavior: this is additive DDL only; no historical rows are
backfilled and no provider attempts or payloads are fabricated.
Locking risk: CREATE TABLE and index DDL acquire ordinary PostgreSQL schema
locks on the new table and briefly inspect referenced graph metadata.
Rollback behavior: downgrade drops only A1 indexes and the new table; existing
clinical, extraction, and quarantine tables are untouched.
Validation approach: static ORM/migration parity checks plus disposable real
PostgreSQL upgrade, constraint-negative, downgrade, and re-upgrade probes.
Forward-fix approach: repair any discovered issue with a new forward-only
revision; never edit an applied parent or stamp around a failed migration.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260818_async_provider_jobs"
down_revision = "20260817_failure_quarantine"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_provider_jobs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider_adapter", sa.String(length=32), nullable=False),
        sa.Column("provider_contract_version", sa.String(length=64), nullable=False),
        sa.Column("provider_model_version", sa.String(length=64), nullable=True),
        sa.Column("provider_job_id", sa.String(length=256), nullable=True),
        sa.Column("client_request_token_digest", sa.String(length=64), nullable=False),
        sa.Column("provider_request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=40),
            nullable=False,
            server_default=sa.text("'CREATED'"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "response_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "result_retrieval_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("expected_page_count", sa.Integer(), nullable=True),
        sa.Column("observed_page_count", sa.Integer(), nullable=True),
        sa.Column(
            "reconciliation_attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "reconciliation_deadline_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_reconcile_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_job_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "result_retrieval_deadline_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("provider_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "supersedes_provider_attempt_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("supersession_reason_code", sa.String(length=64), nullable=True),
        sa.Column("supersession_idempotency_key", sa.String(length=192), nullable=True),
        sa.Column("supersession_request_hash", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["job_id", "tenant_id", "patient_id", "source_document_id"],
            [
                "extraction_jobs.id",
                "extraction_jobs.tenant_id",
                "extraction_jobs.patient_id",
                "extraction_jobs.document_id",
            ],
            name="fk_extraction_provider_jobs_authoritative_job_graph",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id", "tenant_id", "patient_id"],
            [
                "document_storage.id",
                "document_storage.tenant_id",
                "document_storage.patient_id",
            ],
            name="fk_extraction_provider_jobs_authoritative_document_graph",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "supersedes_provider_attempt_id",
                "job_id",
                "tenant_id",
                "patient_id",
                "source_document_id",
            ],
            [
                "extraction_provider_jobs.id",
                "extraction_provider_jobs.job_id",
                "extraction_provider_jobs.tenant_id",
                "extraction_provider_jobs.patient_id",
                "extraction_provider_jobs.source_document_id",
            ],
            name="fk_extraction_provider_jobs_supersedes_same_graph",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "id",
            "job_id",
            "tenant_id",
            "patient_id",
            "source_document_id",
            name="uq_extraction_provider_jobs_authoritative_graph",
        ),
        sa.UniqueConstraint(
            "job_id",
            "job_attempt_number",
            name="uq_extraction_provider_jobs_logical_attempt",
        ),
        sa.UniqueConstraint(
            "provider_adapter",
            "client_request_token_digest",
            name="uq_extraction_provider_jobs_adapter_token_digest",
        ),
        sa.CheckConstraint(
            "status IN ('CREATED', 'SUBMITTING', 'SUBMITTED', 'IN_PROGRESS', 'LOCAL_WAIT_EXPIRED', 'RECONCILING', 'SUCCEEDED', 'FETCHING_RESULTS', 'VALIDATING_COMPLETE_RESULT', 'COMPLETE', 'FAILED_RETRYABLE', 'FAILED_TERMINAL', 'PROVIDER_UNREACHABLE_MANUAL_REVIEW', 'SUPERSEDED')",
            name="ck_extraction_provider_jobs_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_extraction_provider_jobs_version"),
        sa.CheckConstraint(
            "job_attempt_number >= 1",
            name="ck_extraction_provider_jobs_attempt_positive",
        ),
        sa.CheckConstraint(
            "reconciliation_attempt_count >= 0",
            name="ck_extraction_provider_jobs_reconciliation_attempts_nonnegative",
        ),
        sa.CheckConstraint(
            "(expected_page_count IS NULL OR expected_page_count > 0) AND (observed_page_count IS NULL OR observed_page_count > 0)",
            name="ck_extraction_provider_jobs_page_counts_positive",
        ),
        sa.CheckConstraint(
            "status NOT IN ('SUBMITTED', 'IN_PROGRESS', 'SUCCEEDED', 'FETCHING_RESULTS', 'VALIDATING_COMPLETE_RESULT', 'COMPLETE') OR provider_job_id IS NOT NULL",
            name="ck_extraction_provider_jobs_known_provider_id",
        ),
        sa.CheckConstraint(
            "status <> 'COMPLETE' OR (response_complete = true AND result_retrieval_complete = true AND expected_page_count IS NOT NULL AND observed_page_count IS NOT NULL AND expected_page_count > 0 AND observed_page_count > 0 AND expected_page_count = observed_page_count AND provider_job_id IS NOT NULL)",
            name="ck_extraction_provider_jobs_complete_safety",
        ),
        sa.CheckConstraint(
            "status <> 'SUPERSEDED' OR (superseded_at IS NOT NULL AND supersession_reason_code IS NOT NULL)",
            name="ck_extraction_provider_jobs_superseded_safety",
        ),
        sa.CheckConstraint(
            "supersedes_provider_attempt_id IS NULL OR (supersession_reason_code IS NOT NULL AND supersession_idempotency_key IS NOT NULL AND supersession_request_hash IS NOT NULL)",
            name="ck_extraction_provider_jobs_replacement_metadata",
        ),
        sa.CheckConstraint(
            "supersedes_provider_attempt_id IS NULL OR supersedes_provider_attempt_id <> id",
            name="ck_extraction_provider_jobs_no_self_supersession",
        ),
        sa.CheckConstraint(
            "status <> 'PROVIDER_UNREACHABLE_MANUAL_REVIEW' OR (response_complete = false AND result_retrieval_complete = false)",
            name="ck_extraction_provider_jobs_manual_review_incomplete",
        ),
        sa.CheckConstraint(
            "status NOT IN ('FAILED_RETRYABLE', 'FAILED_TERMINAL', 'PROVIDER_UNREACHABLE_MANUAL_REVIEW', 'SUPERSEDED') OR (response_complete = false AND result_retrieval_complete = false)",
            name="ck_extraction_provider_jobs_failed_incomplete",
        ),
    )
    op.create_index(
        "uq_extraction_provider_jobs_provider_job_id",
        "extraction_provider_jobs",
        ["provider_adapter", "provider_job_id"],
        unique=True,
        postgresql_where=sa.text("provider_job_id IS NOT NULL"),
    )
    op.create_index(
        "uq_extraction_provider_jobs_supersession_key",
        "extraction_provider_jobs",
        ["job_id", "supersession_idempotency_key"],
        unique=True,
        postgresql_where=sa.text("supersession_idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_extraction_provider_jobs_reconciliation_claim",
        "extraction_provider_jobs",
        ["status", "next_reconcile_at", "id"],
        unique=False,
        postgresql_where=sa.text("status IN ('LOCAL_WAIT_EXPIRED', 'RECONCILING')"),
    )
    op.create_index(
        "ix_extraction_provider_jobs_job_status",
        "extraction_provider_jobs",
        ["job_id", "status"],
    )
    op.create_index(
        "ix_extraction_provider_jobs_tenant_patient_job",
        "extraction_provider_jobs",
        ["tenant_id", "patient_id", "job_id"],
    )


def downgrade() -> None:
    for index_name in (
        "ix_extraction_provider_jobs_tenant_patient_job",
        "ix_extraction_provider_jobs_job_status",
        "ix_extraction_provider_jobs_reconciliation_claim",
        "uq_extraction_provider_jobs_supersession_key",
        "uq_extraction_provider_jobs_provider_job_id",
    ):
        op.drop_index(index_name, table_name="extraction_provider_jobs")
    op.drop_table("extraction_provider_jobs")
