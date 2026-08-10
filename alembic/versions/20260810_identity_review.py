"""Add metadata-only identity quarantine review records.

Revision ID: 20260810_identity_review
Revises: 20260806_eligibility_reason

Purpose: persist non-release identity-review cases, route bindings,
dispositions, and durable mutation idempotency without identity or clinical
payloads.
Preconditions: the repository and database have the single prior head
20260806_eligibility_reason.
Existing-data behavior: no review case, identity decision, or clinical row is
manufactured or rewritten.
Locking risk: ordinary PostgreSQL DDL locks while four new tables are created.
Rollback position: technical downgrade drops only these new tables; production
systems containing audit/review evidence should use a forward fix instead.
Validation query: inspect pg_constraint, pg_indexes, and information_schema for
the four identity_review_* tables.
Forward-fix strategy: correct defects in a later revision; never stamp past a
failed migration or rewrite applied history.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260810_identity_review"
down_revision = "20260806_eligibility_reason"
branch_labels = None
depends_on = None


_CASE_REASONS = (
    "identity_reason_codes <@ ARRAY['DOCUMENT_IDENTITY_MISMATCH', "
    "'CANONICAL_IDENTITY_UNAVAILABLE']::varchar[] "
    "AND cardinality(identity_reason_codes) > 0"
)
_DISPOSITION_REASONS = (
    "reason_codes <@ ARRAY['DOCUMENT_IDENTITY_MISMATCH', "
    "'CANONICAL_IDENTITY_UNAVAILABLE', 'VERIFIED_IDENTIFIER_REQUIRED', "
    "'POSSIBLE_CROSS_PATIENT_DOCUMENT', 'POSSIBLE_PRIVACY_INCIDENT', "
    "'IDENTITY_REVIEW_INCONCLUSIVE', "
    "'DOCUMENT_REJECTED_FOR_BOUND_PATIENT']::varchar[] "
    "AND cardinality(reason_codes) > 0"
)


def upgrade() -> None:
    op.create_table(
        "identity_review_cases",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("source_document_id", sa.UUID(), nullable=False),
        sa.Column("original_uploader_id", sa.String(64), nullable=True),
        sa.Column("original_authorization_provider_id", sa.String(64), nullable=True),
        sa.Column("source_consent_request_id", sa.String(64), nullable=True),
        sa.Column(
            "identity_reason_codes",
            postgresql.ARRAY(sa.String(64)),
            nullable=False,
        ),
        sa.Column("assigned_reviewer_id", sa.String(64), nullable=True),
        sa.Column("assigned_reviewer_role", sa.String(32), nullable=True),
        sa.Column("review_session_binding", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("creation_idempotency_key", sa.String(192), nullable=False),
        sa.Column("creation_operation_hash", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.String(32), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('PENDING', 'IN_REVIEW', 'RESOLVED_NO_RELEASE', 'ESCALATED')",
            name="ck_identity_review_cases_status",
        ),
        sa.CheckConstraint(
            "version > 0", name="ck_identity_review_cases_version_positive"
        ),
        sa.CheckConstraint(
            "char_length(creation_operation_hash) = 64",
            name="ck_identity_review_cases_operation_hash_length",
        ),
        sa.CheckConstraint(
            "review_session_binding IS NULL OR char_length(review_session_binding) = 64",
            name="ck_identity_review_cases_session_binding_length",
        ),
        sa.CheckConstraint(_CASE_REASONS, name="ck_identity_review_cases_reason_codes"),
        sa.CheckConstraint(
            "(status = 'PENDING' AND assigned_reviewer_id IS NULL "
            "AND assigned_reviewer_role IS NULL AND review_session_binding IS NULL "
            "AND claimed_at IS NULL AND resolved_at IS NULL) OR "
            "(status = 'IN_REVIEW' AND assigned_reviewer_id IS NOT NULL "
            "AND assigned_reviewer_role = 'identity_reviewer' "
            "AND review_session_binding IS NOT NULL AND claimed_at IS NOT NULL "
            "AND resolved_at IS NULL) OR "
            "(status IN ('RESOLVED_NO_RELEASE', 'ESCALATED') "
            "AND assigned_reviewer_id IS NOT NULL "
            "AND assigned_reviewer_role = 'identity_reviewer' "
            "AND review_session_binding IS NOT NULL AND claimed_at IS NOT NULL "
            "AND resolved_at IS NOT NULL)",
            name="ck_identity_review_cases_assignment_state",
        ),
        sa.CheckConstraint(
            "assigned_reviewer_id IS NULL OR "
            "(assigned_reviewer_id IS DISTINCT FROM original_uploader_id "
            "AND assigned_reviewer_id IS DISTINCT FROM original_authorization_provider_id)",
            name="ck_identity_review_cases_reviewer_separation",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["extraction_jobs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_document_id"], ["document_storage.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_identity_review_cases_job"),
        sa.UniqueConstraint(
            "tenant_id",
            "creation_idempotency_key",
            name="uq_identity_review_cases_tenant_idempotency",
        ),
    )
    op.create_index(
        "ix_identity_review_cases_tenant_status",
        "identity_review_cases",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_identity_review_cases_reviewer_status",
        "identity_review_cases",
        ["assigned_reviewer_id", "status"],
    )
    op.create_index(
        "ix_identity_review_cases_patient", "identity_review_cases", ["patient_id"]
    )
    op.create_index(
        "ix_identity_review_cases_document",
        "identity_review_cases",
        ["source_document_id"],
    )

    op.create_table(
        "identity_review_case_routes",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("routing_id", sa.UUID(), nullable=False),
        sa.Column("decision_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("source_document_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["identity_review_cases.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["routing_id"], ["extraction_routing.id"], ondelete="RESTRICT"
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
        sa.UniqueConstraint("routing_id", name="uq_identity_review_case_routes_route"),
        sa.UniqueConstraint(
            "decision_id", name="uq_identity_review_case_routes_decision"
        ),
        sa.UniqueConstraint(
            "case_id",
            "routing_id",
            "decision_id",
            name="uq_identity_review_case_route",
        ),
    )
    op.create_index(
        "ix_identity_review_case_routes_case",
        "identity_review_case_routes",
        ["case_id"],
    )
    op.create_index(
        "ix_identity_review_case_routes_job",
        "identity_review_case_routes",
        ["job_id"],
    )

    op.create_table(
        "identity_review_dispositions",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("patient_id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("reviewer_id", sa.String(64), nullable=False),
        sa.Column("reviewer_role", sa.String(32), nullable=False),
        sa.Column("outcome", sa.String(48), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("prior_case_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(192), nullable=False),
        sa.Column("operation_hash", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.String(32), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reviewer_role = 'identity_reviewer'",
            name="ck_identity_review_dispositions_reviewer_role",
        ),
        sa.CheckConstraint(
            "outcome IN ('REJECTED_FOR_BOUND_PATIENT', 'VERIFIED_IDENTITY_REQUIRED', "
            "'SECURITY_ESCALATION_REQUIRED', 'INSUFFICIENT_IDENTITY_EVIDENCE')",
            name="ck_identity_review_dispositions_outcome",
        ),
        sa.CheckConstraint(
            _DISPOSITION_REASONS,
            name="ck_identity_review_dispositions_reason_codes",
        ),
        sa.CheckConstraint(
            "((outcome = 'REJECTED_FOR_BOUND_PATIENT' AND reason_codes <@ ARRAY["
            "'DOCUMENT_IDENTITY_MISMATCH', 'POSSIBLE_CROSS_PATIENT_DOCUMENT', "
            "'DOCUMENT_REJECTED_FOR_BOUND_PATIENT']::varchar[]) OR "
            "(outcome IN ('VERIFIED_IDENTITY_REQUIRED', 'INSUFFICIENT_IDENTITY_EVIDENCE') "
            "AND reason_codes <@ ARRAY['CANONICAL_IDENTITY_UNAVAILABLE', "
            "'VERIFIED_IDENTIFIER_REQUIRED', 'IDENTITY_REVIEW_INCONCLUSIVE']::varchar[]) OR "
            "(outcome = 'SECURITY_ESCALATION_REQUIRED' AND reason_codes <@ ARRAY["
            "'POSSIBLE_CROSS_PATIENT_DOCUMENT', 'POSSIBLE_PRIVACY_INCIDENT']::varchar[]))",
            name="ck_identity_review_dispositions_outcome_reasons",
        ),
        sa.CheckConstraint(
            "prior_case_version > 0",
            name="ck_identity_review_dispositions_prior_version_positive",
        ),
        sa.CheckConstraint(
            "char_length(operation_hash) = 64",
            name="ck_identity_review_dispositions_operation_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["identity_review_cases.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", name="uq_identity_review_dispositions_case"),
        sa.UniqueConstraint(
            "case_id",
            "prior_case_version",
            name="uq_identity_review_dispositions_case_version",
        ),
        sa.UniqueConstraint(
            "case_id",
            "idempotency_key",
            name="uq_identity_review_dispositions_idempotency",
        ),
    )
    op.create_index(
        "ix_identity_review_dispositions_reviewer",
        "identity_review_dispositions",
        ["reviewer_id"],
    )

    op.create_table(
        "identity_review_operations",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(64), nullable=False),
        sa.Column("actor_role", sa.String(32), nullable=False),
        sa.Column("idempotency_key", sa.String(192), nullable=False),
        sa.Column("operation_hash", sa.String(64), nullable=False),
        sa.Column("prior_version", sa.Integer(), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "operation IN ('CLAIM', 'RECOVER_SESSION', 'SUBMIT_DISPOSITION')",
            name="ck_identity_review_operations_type",
        ),
        sa.CheckConstraint(
            "prior_version > 0 AND result_version > prior_version",
            name="ck_identity_review_operations_versions",
        ),
        sa.CheckConstraint(
            "char_length(operation_hash) = 64",
            name="ck_identity_review_operations_hash_length",
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["identity_review_cases.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "case_id",
            "operation",
            "idempotency_key",
            name="uq_identity_review_operations_idempotency",
        ),
    )
    op.create_index(
        "ix_identity_review_operations_case",
        "identity_review_operations",
        ["case_id"],
    )
    op.create_index(
        "ix_identity_review_operations_actor",
        "identity_review_operations",
        ["actor_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_identity_review_operations_actor",
        table_name="identity_review_operations",
    )
    op.drop_index(
        "ix_identity_review_operations_case",
        table_name="identity_review_operations",
    )
    op.drop_table("identity_review_operations")
    op.drop_index(
        "ix_identity_review_dispositions_reviewer",
        table_name="identity_review_dispositions",
    )
    op.drop_table("identity_review_dispositions")
    op.drop_index(
        "ix_identity_review_case_routes_job",
        table_name="identity_review_case_routes",
    )
    op.drop_index(
        "ix_identity_review_case_routes_case",
        table_name="identity_review_case_routes",
    )
    op.drop_table("identity_review_case_routes")
    op.drop_index(
        "ix_identity_review_cases_document", table_name="identity_review_cases"
    )
    op.drop_index(
        "ix_identity_review_cases_patient", table_name="identity_review_cases"
    )
    op.drop_index(
        "ix_identity_review_cases_reviewer_status",
        table_name="identity_review_cases",
    )
    op.drop_index(
        "ix_identity_review_cases_tenant_status",
        table_name="identity_review_cases",
    )
    op.drop_table("identity_review_cases")
