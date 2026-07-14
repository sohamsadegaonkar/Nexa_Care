"""add stable patient authentication identity mappings

Revision ID: 20260715_patient_auth_identity
Revises: 20260714_provider_schema
Create Date: 2026-07-15 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260715_patient_auth_identity"
down_revision: Union[str, None] = "20260714_provider_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "patient_auth_identities",
        sa.Column(
            "identity_id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("patients.patient_uuid", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_subject", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "provider",
            "provider_subject",
            name="uq_patient_auth_identity_provider_subject",
        ),
    )
    op.create_index(
        "ix_patient_auth_identities_patient_id",
        "patient_auth_identities",
        ["patient_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_patient_auth_identities_patient_id",
        table_name="patient_auth_identities",
    )
    op.drop_table("patient_auth_identities")
