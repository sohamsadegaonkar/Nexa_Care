"""Add durable patient registration recovery manual-review state.

Revision ID: 20260910_registration_recovery_review
Revises: 20260909_device_trust_lifecycle
Create Date: 2026-09-10 23:45:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260910_registration_recovery_review"
down_revision: Union[str, None] = "20260909_device_trust_lifecycle"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REASON_SQL = (
    "ARRAY['MISSING_RECORD_ANCHOR','MERGED_IDENTITY_REBIND_REQUIRED',"
    "'IDENTITY_REVOKED','PATIENT_DELETED_WITHOUT_MERGE','ERASURE_STATE_PRESENT',"
    "'MULTIPLE_IDENTITIES','MERGE_AMBIGUOUS','GRAPH_STATE_CHANGED',"
    "'SECURITY_CONCERN']::varchar[]"
)


def upgrade() -> None:
    op.create_table(
        "patient_registration_recovery_review_cases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("case_reference", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_subject_hash", sa.String(64), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("graph_fingerprint", sa.String(64), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("assigned_reviewer_id", sa.String(128), nullable=True),
        sa.Column("assigned_reviewer_role", sa.String(64), nullable=True),
        sa.Column("reviewer_authority_version", sa.String(64), nullable=True),
        sa.Column("review_session_binding", sa.String(64), nullable=True),
        sa.Column("creation_idempotency_key", sa.String(192), nullable=False),
        sa.Column("creation_operation_hash", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.patient_uuid"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "case_reference", name="uq_registration_recovery_review_case_reference"
        ),
        sa.UniqueConstraint(
            "provider",
            "provider_subject_hash",
            "graph_fingerprint",
            name="uq_registration_recovery_review_graph",
        ),
        sa.UniqueConstraint(
            "creation_idempotency_key",
            name="uq_registration_recovery_review_creation_idempotency",
        ),
        sa.CheckConstraint(
            "provider = 'supabase'", name="ck_registration_recovery_review_provider"
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','IN_REVIEW','RESOLVED','REJECTED','SECURITY_ESCALATED')",
            name="ck_registration_recovery_review_status",
        ),
        sa.CheckConstraint(
            "version > 0", name="ck_registration_recovery_review_version"
        ),
        sa.CheckConstraint(
            "char_length(provider_subject_hash) = 64 AND "
            "char_length(graph_fingerprint) = 64 AND "
            "char_length(creation_operation_hash) = 64",
            name="ck_registration_recovery_review_hash_lengths",
        ),
        sa.CheckConstraint(
            f"reason_codes <@ {_REASON_SQL} AND cardinality(reason_codes) > 0",
            name="ck_registration_recovery_review_reasons",
        ),
        sa.CheckConstraint(
            "review_session_binding IS NULL OR char_length(review_session_binding) = 64",
            name="ck_registration_recovery_review_session_binding",
        ),
        sa.CheckConstraint(
            "(status = 'PENDING' AND assigned_reviewer_id IS NULL "
            "AND assigned_reviewer_role IS NULL AND reviewer_authority_version IS NULL "
            "AND review_session_binding IS NULL AND claimed_at IS NULL "
            "AND resolved_at IS NULL) OR "
            "(status = 'IN_REVIEW' AND assigned_reviewer_id IS NOT NULL "
            "AND assigned_reviewer_role = 'registration_recovery_reviewer' "
            "AND reviewer_authority_version IS NOT NULL "
            "AND review_session_binding IS NOT NULL AND claimed_at IS NOT NULL "
            "AND resolved_at IS NULL) OR "
            "(status IN ('RESOLVED','REJECTED','SECURITY_ESCALATED') "
            "AND assigned_reviewer_id IS NOT NULL "
            "AND assigned_reviewer_role = 'registration_recovery_reviewer' "
            "AND reviewer_authority_version IS NOT NULL "
            "AND review_session_binding IS NOT NULL AND claimed_at IS NOT NULL "
            "AND resolved_at IS NOT NULL)",
            name="ck_registration_recovery_review_assignment_state",
        ),
    )
    op.create_index(
        "ix_registration_recovery_review_status",
        "patient_registration_recovery_review_cases",
        ["status"],
    )
    op.create_index(
        "ix_registration_recovery_review_patient",
        "patient_registration_recovery_review_cases",
        ["patient_id"],
    )
    op.create_index(
        "ix_registration_recovery_review_reviewer",
        "patient_registration_recovery_review_cases",
        ["assigned_reviewer_id", "status"],
    )

    op.create_table(
        "patient_registration_recovery_review_dispositions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_id", sa.String(128), nullable=False),
        sa.Column("reviewer_role", sa.String(64), nullable=False),
        sa.Column("reviewer_authority_version", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("reason_codes", postgresql.ARRAY(sa.String(64)), nullable=False),
        sa.Column("prior_case_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(192), nullable=False),
        sa.Column("operation_hash", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["patient_registration_recovery_review_cases.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "case_id", name="uq_registration_recovery_review_disposition_case"
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_registration_recovery_review_disposition_idempotency",
        ),
        sa.CheckConstraint(
            "reviewer_role = 'registration_recovery_reviewer'",
            name="ck_registration_recovery_disposition_role",
        ),
        sa.CheckConstraint(
            "outcome IN ('RESTORE_MISSING_RECORD_ANCHOR','REBIND_MERGED_IDENTITY',"
            "'NO_REPAIR','SECURITY_ESCALATION_REQUIRED')",
            name="ck_registration_recovery_disposition_outcome",
        ),
        sa.CheckConstraint(
            f"reason_codes <@ {_REASON_SQL} AND cardinality(reason_codes) > 0",
            name="ck_registration_recovery_disposition_reasons",
        ),
        sa.CheckConstraint(
            "prior_case_version > 0 AND char_length(operation_hash) = 64",
            name="ck_registration_recovery_disposition_operation",
        ),
    )
    op.create_index(
        "ix_registration_recovery_disposition_case",
        "patient_registration_recovery_review_dispositions",
        ["case_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_registration_recovery_disposition_case",
        table_name="patient_registration_recovery_review_dispositions",
    )
    op.drop_table("patient_registration_recovery_review_dispositions")
    op.drop_index(
        "ix_registration_recovery_review_reviewer",
        table_name="patient_registration_recovery_review_cases",
    )
    op.drop_index(
        "ix_registration_recovery_review_patient",
        table_name="patient_registration_recovery_review_cases",
    )
    op.drop_index(
        "ix_registration_recovery_review_status",
        table_name="patient_registration_recovery_review_cases",
    )
    op.drop_table("patient_registration_recovery_review_cases")
