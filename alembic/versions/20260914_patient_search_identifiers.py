"""Add privacy-preserving patient search identifier authority.

Revision ID: 20260914_patient_search_identifiers
Revises: 20260910_registration_recovery_review
Create Date: 2026-09-14 22:05:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260914_patient_search_identifiers"
down_revision: Union[str, None] = "20260910_registration_recovery_review"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "patient_search_identifiers",
        sa.Column(
            "identifier_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("identifier_type", sa.String(16), nullable=False),
        sa.Column("normalization_version", sa.Integer(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("value_hmac", sa.String(64), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(32), nullable=True),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["patients.patient_uuid"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["patient_auth_identities.identity_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "identifier_type IN ('PHONE')",
            name="ck_patient_search_identifier_type",
        ),
        sa.CheckConstraint(
            "normalization_version > 0 AND key_version > 0",
            name="ck_patient_search_identifier_versions",
        ),
        sa.CheckConstraint(
            "char_length(value_hmac) = 64",
            name="ck_patient_search_identifier_hmac_length",
        ),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revocation_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revocation_reason IN "
            "('SUPERSEDED','IDENTITY_REVOKED','IDENTITY_REBOUND','PATIENT_ERASED',"
            "'AUTHORITY_CONFLICT','SOURCE_REVERIFICATION_FAILED','PATIENT_OPT_OUT',"
            "'ADMINISTRATIVE'))",
            name="ck_patient_search_identifier_lifecycle",
        ),
    )
    op.create_index(
        "ix_patient_search_identifier_patient",
        "patient_search_identifiers",
        ["patient_id", "identifier_type"],
    )
    op.create_index(
        "ix_patient_search_identifier_identity",
        "patient_search_identifiers",
        ["identity_id"],
    )
    op.create_index(
        "uq_patient_search_identifier_active_patient_type",
        "patient_search_identifiers",
        ["patient_id", "identifier_type"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "uq_patient_search_identifier_active_value",
        "patient_search_identifiers",
        ["identifier_type", "key_version", "value_hmac"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_patient_search_identifier_active_value",
        table_name="patient_search_identifiers",
    )
    op.drop_index(
        "uq_patient_search_identifier_active_patient_type",
        table_name="patient_search_identifiers",
    )
    op.drop_index(
        "ix_patient_search_identifier_identity",
        table_name="patient_search_identifiers",
    )
    op.drop_index(
        "ix_patient_search_identifier_patient",
        table_name="patient_search_identifiers",
    )
    op.drop_table("patient_search_identifiers")
