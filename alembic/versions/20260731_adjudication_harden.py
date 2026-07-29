"""Harden adjudication session, hash, attempt, and resource integrity.

Revision ID: 20260731_adjudication_harden
Revises: 20260730_source_adjudicate

Purpose: enforce Milestone 4.1 adjudication relationships.
Preconditions: existing Milestone 4 rows satisfy the new integrity rules.
Existing-data behavior: no clinical data or decisions are rewritten or created.
Locking risk: ordinary PostgreSQL DDL locks on the adjudication tables.
Rollback position: downgrade removes only constraints added here.
Validation query: inspect pg_constraint for adjudication constraint names.
Forward-fix strategy: use a later forward migration; never stamp past failure.
"""

from alembic import op

revision = "20260731_adjudication_harden"
down_revision = "20260730_source_adjudicate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "fk_adjudication_cases_accepted_submission",
        "adjudication_cases",
        type_="foreignkey",
    )
    op.create_check_constraint(
        "ck_adjudication_cases_version_positive",
        "adjudication_cases",
        "version > 0",
    )
    op.create_check_constraint(
        "ck_adjudication_cases_operation_hash_length",
        "adjudication_cases",
        "char_length(operation_hash) = 64",
    )
    op.create_check_constraint(
        "ck_adjudication_submissions_source_binding",
        "adjudication_submissions",
        "(routing_id IS NULL AND decision_id IS NULL) OR "
        "(routing_id IS NOT NULL AND decision_id IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_adjudication_submissions_attempt_positive",
        "adjudication_submissions",
        "attempt_number > 0",
    )
    op.create_check_constraint(
        "ck_adjudication_submissions_content_hash_length",
        "adjudication_submissions",
        "char_length(content_hash) = 64",
    )
    op.create_unique_constraint(
        "uq_adjudication_submissions_case_id_id",
        "adjudication_submissions",
        ["case_id", "id"],
    )
    op.create_foreign_key(
        "fk_adjudication_cases_accepted_submission_same_case",
        "adjudication_cases",
        "adjudication_submissions",
        ["id", "accepted_submission_id"],
        ["case_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_adjudication_submissions_document",
        "adjudication_submissions",
        "document_storage",
        ["source_document_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_adjudication_submissions_job",
        "adjudication_submissions",
        "extraction_jobs",
        ["job_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_adjudication_submissions_routing",
        "adjudication_submissions",
        "extraction_routing",
        ["routing_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_adjudication_submissions_decision",
        "adjudication_submissions",
        "extraction_decisions",
        ["decision_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    for name in (
        "fk_adjudication_submissions_decision",
        "fk_adjudication_submissions_routing",
        "fk_adjudication_submissions_job",
        "fk_adjudication_submissions_document",
    ):
        op.drop_constraint(
            name,
            "adjudication_submissions",
            type_="foreignkey",
        )
    op.drop_constraint(
        "fk_adjudication_cases_accepted_submission_same_case",
        "adjudication_cases",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_adjudication_submissions_case_id_id",
        "adjudication_submissions",
        type_="unique",
    )
    for name in (
        "ck_adjudication_submissions_content_hash_length",
        "ck_adjudication_submissions_attempt_positive",
        "ck_adjudication_submissions_source_binding",
        "ck_adjudication_cases_operation_hash_length",
        "ck_adjudication_cases_version_positive",
    ):
        op.drop_constraint(
            name,
            (
                "adjudication_cases"
                if name.startswith("ck_adjudication_cases")
                else "adjudication_submissions"
            ),
            type_="check",
        )
    op.create_foreign_key(
        "fk_adjudication_cases_accepted_submission",
        "adjudication_cases",
        "adjudication_submissions",
        ["accepted_submission_id"],
        ["id"],
        ondelete="RESTRICT",
    )
