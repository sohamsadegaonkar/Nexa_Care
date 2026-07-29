"""Persist safe extraction decision and lane routing metadata.

Revision ID: 20260729_extract_lane_route
Revises: 20260727_doc_process_bind

Purpose: Add immutable decision projections and separate mutable routing state.
Preconditions: The current pipeline and document tables exist.
Existing-data behavior: No historical rows are manufactured or backfilled.
Locking risk: New-table and new-index DDL only; no existing table rewrite.
Rollback position: Drop only the two Milestone 3 tables.
Validation query: Inspect constraints/indexes and verify both tables are empty.
Forward-fix strategy: Apply a later additive migration; never rewrite decisions.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260729_extract_lane_route"
down_revision = "20260727_doc_process_bind"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "extraction_decisions",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("decision_contract_version", sa.String(16), nullable=False),
        sa.Column("evidence_contract_version", sa.String(16), nullable=False),
        sa.Column("evidence_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("source_document_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("workflow_id", sa.String(64), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        sa.Column("attempt_id", sa.String(96), nullable=False),
        sa.Column("lane", sa.String(24), nullable=False),
        sa.Column(
            "reason_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("policy_configuration_hash", sa.String(64), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluator_version", sa.String(64), nullable=False),
        sa.Column("auto_commit_feature_enabled", sa.Boolean(), nullable=False),
        sa.Column("earlier_decision_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lane IN ('SOURCE_ONLY', 'QUARANTINE')",
            name="ck_extraction_decisions_safe_lane",
        ),
        sa.CheckConstraint(
            "auto_commit_feature_enabled = false",
            name="ck_extraction_decisions_auto_commit_disabled",
        ),
        sa.ForeignKeyConstraint(
            ["earlier_decision_id"], ["extraction_decisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["extraction_jobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["document_storage.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_extraction_decisions_tenant_patient",
        "extraction_decisions",
        ["tenant_id", "patient_id"],
    )
    op.create_index(
        "ix_extraction_decisions_job_lane",
        "extraction_decisions",
        ["job_id", "lane"],
    )
    op.create_index(
        "ix_extraction_decisions_evidence",
        "extraction_decisions",
        ["evidence_id"],
    )

    op.create_table(
        "extraction_routing",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("decision_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("source_document_id", sa.UUID(), nullable=False),
        sa.Column("lane", sa.String(24), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("routed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "quarantine_review_deadline", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_reference", sa.String(128), nullable=True),
        sa.Column("idempotency_key", sa.String(192), nullable=False),
        sa.Column("operation_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lane IN ('SOURCE_ONLY', 'QUARANTINE')",
            name="ck_extraction_routing_safe_lane",
        ),
        sa.CheckConstraint(
            "status IN ('SOURCE_RETAINED', 'QUARANTINE_PENDING', "
            "'QUARANTINE_ESCALATED')",
            name="ck_extraction_routing_status",
        ),
        sa.CheckConstraint(
            "(lane = 'SOURCE_ONLY' AND status = 'SOURCE_RETAINED' "
            "AND quarantine_review_deadline IS NULL) OR "
            "(lane = 'QUARANTINE' AND status IN "
            "('QUARANTINE_PENDING', 'QUARANTINE_ESCALATED') "
            "AND quarantine_review_deadline IS NOT NULL)",
            name="ck_extraction_routing_lane_state",
        ),
        sa.CheckConstraint(
            "(status = 'QUARANTINE_ESCALATED' AND escalated_at IS NOT NULL) OR "
            "(status <> 'QUARANTINE_ESCALATED' AND escalated_at IS NULL)",
            name="ck_extraction_routing_escalation_time",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["extraction_decisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["extraction_jobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["document_storage.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id", name="uq_extraction_routing_decision"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_extraction_routing_idempotency"
        ),
    )
    op.create_index(
        "ix_extraction_routing_tenant_patient",
        "extraction_routing",
        ["tenant_id", "patient_id"],
    )
    op.create_index(
        "ix_extraction_routing_job_lane",
        "extraction_routing",
        ["job_id", "lane"],
    )
    op.create_index(
        "ix_extraction_routing_unresolved_quarantine",
        "extraction_routing",
        ["status", "quarantine_review_deadline"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_routing_unresolved_quarantine",
        table_name="extraction_routing",
    )
    op.drop_index("ix_extraction_routing_job_lane", table_name="extraction_routing")
    op.drop_index(
        "ix_extraction_routing_tenant_patient", table_name="extraction_routing"
    )
    op.drop_table("extraction_routing")
    op.drop_index("ix_extraction_decisions_evidence", table_name="extraction_decisions")
    op.drop_index("ix_extraction_decisions_job_lane", table_name="extraction_decisions")
    op.drop_index(
        "ix_extraction_decisions_tenant_patient",
        table_name="extraction_decisions",
    )
    op.drop_table("extraction_decisions")
