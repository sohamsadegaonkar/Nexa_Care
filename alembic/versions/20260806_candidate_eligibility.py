"""Persist non-destructive extraction candidate eligibility metadata.

Revision ID: 20260806_candidate_eligibility
Revises: 20260801_textract_candidates
"""

import sqlalchemy as sa
from alembic import op

revision = "20260806_candidate_eligibility"
down_revision = "20260801_textract_candidates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "extraction_candidates",
        sa.Column("routing_eligible", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "extraction_candidates",
        sa.Column("eligibility_reason_code", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "extraction_candidates",
        sa.Column("eligibility_policy_version", sa.String(length=16), nullable=True),
    )
    op.execute(
        "UPDATE extraction_candidates SET routing_eligible = TRUE "
        "WHERE routing_eligible IS NULL"
    )
    op.execute(
        "UPDATE extraction_candidates SET eligibility_policy_version = 'v1' "
        "WHERE eligibility_policy_version IS NULL"
    )
    op.alter_column("extraction_candidates", "routing_eligible", nullable=False)
    op.alter_column(
        "extraction_candidates", "eligibility_policy_version", nullable=False
    )
    op.create_check_constraint(
        "ck_extraction_candidates_eligibility",
        "extraction_candidates",
        "(routing_eligible AND eligibility_reason_code IS NULL) OR "
        "(NOT routing_eligible AND eligibility_reason_code IS NOT NULL AND "
        "eligibility_reason_code = "
        "'INELIGIBLE_QUERY_ONLY_INVALID_FORMAT')",
    )
    op.create_index(
        "ix_extraction_candidates_job_routing_eligible",
        "extraction_candidates",
        ["job_id", "routing_eligible"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_extraction_candidates_job_routing_eligible",
        table_name="extraction_candidates",
    )
    op.drop_constraint(
        "ck_extraction_candidates_eligibility",
        "extraction_candidates",
        type_="check",
    )
    op.drop_column("extraction_candidates", "eligibility_policy_version")
    op.drop_column("extraction_candidates", "eligibility_reason_code")
    op.drop_column("extraction_candidates", "routing_eligible")
