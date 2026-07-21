"""create canonical patient policy table

Revision ID: 20260718_policy_schema
Revises: 20260718_security_governance

The PatientPolicy ORM predates its Alembic table creation.  This dependency
revision supplies the complete pre-runtime schema before
20260719_security_runtime adds compare-and-swap fields.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260718_policy_schema"
down_revision: Union[str, None] = "20260718_security_governance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    public_tables = set(inspector.get_table_names(schema="public"))

    if "patient_policy" in public_tables and "patient_policies" not in public_tables:
        raise RuntimeError(
            "Migration precondition failed: public.patient_policy exists but "
            "the canonical table is public.patient_policies; automatic rename "
            "requires an operator-reviewed schema classification."
        )

    if "patient_policies" not in public_tables:
        op.create_table(
            "patient_policies",
            sa.Column(
                "patient_uuid",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("public.patients.patient_uuid", ondelete="CASCADE"),
                primary_key=True,
                nullable=False,
            ),
            sa.Column(
                "consent_assurance_policy",
                sa.String(),
                nullable=False,
                server_default=sa.text("'standard'"),
            ),
            sa.Column("updated_at", sa.String(), nullable=True),
            schema="public",
        )


def downgrade() -> None:
    op.drop_table("patient_policies", schema="public")
