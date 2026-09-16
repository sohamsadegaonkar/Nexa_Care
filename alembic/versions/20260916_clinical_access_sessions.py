"""Add durable bounded clinical access session lifecycle.

Revision ID: 20260916_clinical_access_sessions
Revises: 20260914_patient_search_identifiers
Create Date: 2026-09-16 19:40:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260916_clinical_access_sessions"
down_revision: Union[str, None] = "20260914_patient_search_identifiers"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "clinical_access_sessions",
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("consent_request_id", sa.String(length=64), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("allowed_operations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider_session_binding_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=64), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("encounter_id", sa.String(length=64), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "token_hash ~ '^[0-9a-f]{64}$'",
            name="ck_clinical_access_session_token_hash",
        ),
        sa.CheckConstraint(
            "provider_session_binding_hash ~ '^[0-9a-f]{64}$'",
            name="ck_clinical_access_session_binding_hash",
        ),
        sa.CheckConstraint(
            "expires_at > issued_at",
            name="ck_clinical_access_session_positive_lifetime",
        ),
        sa.CheckConstraint(
            "scope IN ('clinical','full')",
            name="ck_clinical_access_session_scope",
        ),
        sa.CheckConstraint(
            "policy_version = 'clinical-access-v1'",
            name="ck_clinical_access_session_policy_v1",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(allowed_operations) = 'array' "
            "AND allowed_operations = '[\"READ_CLINICAL_HISTORY\"]'::jsonb",
            name="ck_clinical_access_session_ops_v1",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE','REVOKED')",
            name="ck_clinical_access_session_status",
        ),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND revoked_at IS NULL AND revocation_reason IS NULL) OR "
            "(status = 'REVOKED' AND revoked_at IS NOT NULL AND revocation_reason IS NOT NULL)",
            name="ck_clinical_access_session_revocation_state",
        ),
        sa.CheckConstraint(
            "revocation_reason IS NULL OR revocation_reason IN ("
            "'PATIENT_REVOKED','PROVIDER_TRUST_LOST','PROVIDER_SESSION_ENDED',"
            "'PATIENT_MERGED','PATIENT_DELETED','PATIENT_ERASED',"
            "'CLAIM_FINALIZATION_FAILED','CAPABILITY_INVALIDATED','ADMINISTRATIVE')",
            name="ck_clinical_access_session_revocation_reason",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.patient_uuid"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["provider_identity.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id"], ["hospital_registry.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("session_id"),
        sa.UniqueConstraint(
            "consent_request_id", name="uq_clinical_access_session_consent_request"
        ),
        sa.UniqueConstraint("token_hash", name="uq_clinical_access_session_token_hash"),
    )
    op.create_index(
        "ix_clinical_access_session_patient_active",
        "clinical_access_sessions",
        ["patient_id", "status", "expires_at"],
    )
    op.create_index(
        "ix_clinical_access_session_provider_active",
        "clinical_access_sessions",
        ["provider_id", "hospital_id", "status", "expires_at"],
    )
    op.create_index(
        "ix_clinical_access_session_request",
        "clinical_access_sessions",
        ["consent_request_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_clinical_access_session_request", table_name="clinical_access_sessions")
    op.drop_index(
        "ix_clinical_access_session_provider_active",
        table_name="clinical_access_sessions",
    )
    op.drop_index(
        "ix_clinical_access_session_patient_active",
        table_name="clinical_access_sessions",
    )
    op.drop_table("clinical_access_sessions")
