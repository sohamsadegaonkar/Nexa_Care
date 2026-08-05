"""Allow truthful candidate eligibility failure reason codes.

Revision ID: 20260806_eligibility_reason
Revises: 20260806_candidate_eligibility
"""

from alembic import op

revision = "20260806_eligibility_reason"
down_revision = "20260806_candidate_eligibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_extraction_candidates_eligibility",
        "extraction_candidates",
        type_="check",
    )
    op.create_check_constraint(
        "ck_extraction_candidates_eligibility",
        "extraction_candidates",
        "(routing_eligible AND eligibility_reason_code IS NULL) OR "
        "(NOT routing_eligible AND eligibility_reason_code IS NOT NULL AND "
        "eligibility_reason_code IN ("
        "'INELIGIBLE_QUERY_ONLY_INVALID_FORMAT', "
        "'INELIGIBLE_CLASSIFICATION_FAILED'))",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_extraction_candidates_eligibility",
        "extraction_candidates",
        type_="check",
    )
    # Compatibility fallback only: the previous schema cannot represent an
    # internal classification failure. This is not a clinical reinterpretation.
    op.execute(
        "UPDATE extraction_candidates SET eligibility_reason_code = "
        "'INELIGIBLE_QUERY_ONLY_INVALID_FORMAT' "
        "WHERE eligibility_reason_code = 'INELIGIBLE_CLASSIFICATION_FAILED'"
    )
    op.create_check_constraint(
        "ck_extraction_candidates_eligibility",
        "extraction_candidates",
        "(routing_eligible AND eligibility_reason_code IS NULL) OR "
        "(NOT routing_eligible AND eligibility_reason_code IS NOT NULL AND "
        "eligibility_reason_code = "
        "'INELIGIBLE_QUERY_ONLY_INVALID_FORMAT')",
    )
