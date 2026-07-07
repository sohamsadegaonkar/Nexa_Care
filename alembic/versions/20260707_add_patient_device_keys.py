"""add_patient_device_keys_and_consent_grant_ext

Revision ID: 20260707_device_keys
Revises: 20260706_add_patient_dek_store
Create Date: 2026-07-07 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260707_device_keys"
down_revision: Union[str, None] = "20260706_add_patient_dek_store"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create patient_device_keys table
    op.create_table(
        "patient_device_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("device_public_key", sa.LargeBinary(), nullable=False),
        sa.Column("device_label", sa.String(length=100), nullable=True),
        sa.Column("platform", sa.String(length=20), nullable=False),
        sa.Column("key_algorithm", sa.String(length=32), server_default="ECDSA-P256", nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("enrolled_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("patient_id", "device_public_key", name="uq_patient_device_public_key"),
    )
    op.create_index(op.f("ix_patient_device_keys_patient_id"), "patient_device_keys", ["patient_id"], unique=False)

    # 2. Add request_id and signed_approval_id to consent_grant_log
    op.add_column("consent_grant_log", sa.Column("request_id", sa.String(length=64), nullable=True))
    op.add_column("consent_grant_log", sa.Column("signed_approval_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_consent_grant_log_request_id"), "consent_grant_log", ["request_id"], unique=False)
    op.create_index(op.f("ix_consent_grant_log_signed_approval_id"), "consent_grant_log", ["signed_approval_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_consent_grant_log_signed_approval_id"), table_name="consent_grant_log")
    op.drop_index(op.f("ix_consent_grant_log_request_id"), table_name="consent_grant_log")
    op.drop_column("consent_grant_log", "signed_approval_id")
    op.drop_column("consent_grant_log", "request_id")
    op.drop_index(op.f("ix_patient_device_keys_patient_id"), table_name="patient_device_keys")
    op.drop_table("patient_device_keys")
