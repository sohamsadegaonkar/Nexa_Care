"""Add field_corrections table for WS5 evaluation dataset.

Revision ID: 20260707_corrections
Revises: 20260707_pipeline
Create Date: 2026-07-07 18:00:00.000000

"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260707_corrections"
down_revision = "20260707_pipeline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "field_corrections",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column("original_value", sa.String(length=512), nullable=False),
        sa.Column("corrected_value", sa.String(length=512), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("corrected_by", sa.String(length=64), nullable=True),
        sa.Column("corrected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["field_id"], ["extracted_fields.id"], name=op.f("fk_field_corrections_field_id_extracted_fields"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_field_corrections")),
    )
    op.create_index(op.f("ix_field_corrections_field_id"), "field_corrections", ["field_id"], unique=False)
    op.create_index(op.f("ix_field_corrections_job_id"), "field_corrections", ["job_id"], unique=False)


def downgrade() -> None:
    op.drop_table("field_corrections")
