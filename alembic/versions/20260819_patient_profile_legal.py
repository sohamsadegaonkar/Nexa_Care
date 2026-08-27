"""Add patient_profiles and patient_legal_acceptances tables.

Revision ID: 20260819_patient_profile_legal
Revises: 20260819_widen_vault_pii_columns
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "20260819_patient_profile_legal"
down_revision = "20260819_widen_vault_pii_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "patient_profiles",
        sa.Column(
            "patient_id",
            UUID(as_uuid=True),
            sa.ForeignKey("patients.patient_uuid", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("full_name_encrypted", sa.Text(), nullable=False),
        sa.Column("date_of_birth_encrypted", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_table(
        "patient_legal_acceptances",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "patient_id",
            UUID(as_uuid=True),
            sa.ForeignKey("patients.patient_uuid", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("document_version", sa.String(64), nullable=False),
        sa.Column("document_sha256", sa.String(64), nullable=False),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "document_type IN ('TERMS_OF_SERVICE', 'PRIVACY_NOTICE')",
            name="ck_legal_acceptances_document_type",
        ),
        sa.CheckConstraint(
            "document_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_legal_acceptances_sha256_hex",
        ),
        sa.UniqueConstraint(
            "patient_id",
            "document_type",
            "document_version",
            name="uq_legal_acceptance_patient_doc_version",
        ),
        sa.Index("ix_patient_legal_acceptances_patient_id", "patient_id"),
    )


def downgrade() -> None:
    op.drop_table("patient_legal_acceptances")
    op.drop_table("patient_profiles")
