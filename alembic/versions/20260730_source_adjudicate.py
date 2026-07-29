"""Add provenance-bound human source adjudication.

Revision ID: 20260730_source_adjudicate
Revises: 20260729_extract_lane_route

No historical cases or submissions are manufactured.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260730_source_adjudicate"
down_revision = "20260729_extract_lane_route"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "adjudication_cases",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("source_document_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("routing_id", sa.UUID(), nullable=True),
        sa.Column("decision_id", sa.UUID(), nullable=True),
        sa.Column("reviewer_id", sa.String(64), nullable=False),
        sa.Column("reviewer_organization_id", sa.UUID(), nullable=False),
        sa.Column("reviewer_role", sa.String(32), nullable=False),
        sa.Column("review_session_id", sa.String(96), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("accepted_submission_id", sa.UUID(), nullable=True),
        sa.Column("clinical_committed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(192), nullable=False),
        sa.Column("operation_hash", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.String(16), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'ACCEPTED', 'REJECTED', "
            "'NEEDS_SPECIALIST_REVIEW')",
            name="ck_adjudication_cases_status",
        ),
        sa.CheckConstraint(
            "(routing_id IS NULL AND decision_id IS NULL) OR "
            "(routing_id IS NOT NULL AND decision_id IS NOT NULL)",
            name="ck_adjudication_cases_source_binding",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"], ["extraction_decisions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["extraction_jobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["routing_id"], ["extraction_routing.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["document_storage.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_adjudication_cases_idempotency"
        ),
    )
    op.create_index(
        "ix_adjudication_cases_tenant_status",
        "adjudication_cases",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_adjudication_cases_reviewer_status",
        "adjudication_cases",
        ["reviewer_id", "status"],
    )
    op.create_index(
        "ix_adjudication_cases_patient", "adjudication_cases", ["patient_id"]
    )
    op.create_index("ix_adjudication_cases_job", "adjudication_cases", ["job_id"])

    op.create_table(
        "adjudication_submissions",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("source_document_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("routing_id", sa.UUID(), nullable=True),
        sa.Column("decision_id", sa.UUID(), nullable=True),
        sa.Column("reviewer_id", sa.String(64), nullable=False),
        sa.Column("reviewer_organization_id", sa.UUID(), nullable=False),
        sa.Column("reviewer_role", sa.String(32), nullable=False),
        sa.Column("review_session_id", sa.String(96), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("clinical_payload", postgresql.JSONB(), nullable=False),
        sa.Column("supersedes_submission_id", sa.UUID(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contract_version", sa.String(16), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("reason_codes", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(192), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('ACCEPTED', 'REJECTED', "
            "'NEEDS_SPECIALIST_REVIEW', 'SUPERSEDED')",
            name="ck_adjudication_submissions_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["adjudication_cases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_submission_id"],
            ["adjudication_submissions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_adjudication_submissions_idempotency"
        ),
        sa.UniqueConstraint(
            "case_id",
            "attempt_number",
            name="uq_adjudication_submissions_case_attempt",
        ),
    )
    op.create_index(
        "ix_adjudication_submissions_case",
        "adjudication_submissions",
        ["case_id"],
    )
    op.create_index(
        "ix_adjudication_submissions_reviewer",
        "adjudication_submissions",
        ["reviewer_id"],
    )
    op.create_foreign_key(
        "fk_adjudication_cases_accepted_submission",
        "adjudication_cases",
        "adjudication_submissions",
        ["accepted_submission_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_adjudication_cases_accepted_submission",
        "adjudication_cases",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_adjudication_submissions_reviewer",
        table_name="adjudication_submissions",
    )
    op.drop_index(
        "ix_adjudication_submissions_case",
        table_name="adjudication_submissions",
    )
    op.drop_table("adjudication_submissions")
    op.drop_index("ix_adjudication_cases_job", table_name="adjudication_cases")
    op.drop_index("ix_adjudication_cases_patient", table_name="adjudication_cases")
    op.drop_index(
        "ix_adjudication_cases_reviewer_status", table_name="adjudication_cases"
    )
    op.drop_index(
        "ix_adjudication_cases_tenant_status", table_name="adjudication_cases"
    )
    op.drop_table("adjudication_cases")
