"""Add operational retry-exhaustion quarantine disposition lifecycle.

Revision ID: 20260817_failure_quarantine
Revises: 20260815_clinical_commit_guard

Purpose: retain value-free operational handling for exhausted provider retries.
Preconditions: current schema is at the declared predecessor.
Existing-data behavior: schema only; legacy quarantined jobs are not backfilled.
Locking risk: ordinary DDL lock while creating the new table and index.
Rollback position: remove only this new lifecycle child table and its indexes.
Validation query: inspect constraints and processor index on the new table.
Forward-fix strategy: add a forward-only revision; never fabricate old cases.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260817_failure_quarantine"
down_revision = "20260815_clinical_commit_guard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_failure_quarantines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason_code", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("review_deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disposition", sa.String(length=48), nullable=True),
        sa.Column("disposed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "disposed_by_provider_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("disposition_idempotency_key", sa.String(length=192), nullable=True),
        sa.Column("disposition_request_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reason_code = 'PROVIDER_RETRY_EXHAUSTED'",
            name="ck_failure_quarantines_reason_code",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ESCALATED', 'DISPOSED')",
            name="ck_failure_quarantines_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_failure_quarantines_version"),
        sa.CheckConstraint(
            "(status = 'PENDING' AND escalated_at IS NULL AND disposition IS NULL AND disposed_at IS NULL AND disposed_by_provider_id IS NULL) OR "
            "(status = 'ESCALATED' AND escalated_at IS NOT NULL AND disposition IS NULL AND disposed_at IS NULL AND disposed_by_provider_id IS NULL) OR "
            "(status = 'DISPOSED' AND escalated_at IS NOT NULL AND disposition IN ('RETAIN_SOURCE_NO_CLINICAL_COMMIT', 'REJECT_PROCESSING_RETAIN_AUDIT') AND disposed_at IS NOT NULL AND disposed_by_provider_id IS NOT NULL)",
            name="ck_failure_quarantines_lifecycle",
        ),
        sa.CheckConstraint(
            "(disposition_idempotency_key IS NULL AND disposition_request_hash IS NULL) OR (disposition_idempotency_key IS NOT NULL AND disposition_request_hash IS NOT NULL)",
            name="ck_failure_quarantines_idempotency_pair",
        ),
        sa.ForeignKeyConstraint(
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_extraction_failure_quarantines_job"),
    )
    op.create_index(
        "ix_failure_quarantines_processor",
        "extraction_failure_quarantines",
        ["status", "review_deadline", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_failure_quarantines_processor", table_name="extraction_failure_quarantines"
    )
    op.drop_table("extraction_failure_quarantines")
