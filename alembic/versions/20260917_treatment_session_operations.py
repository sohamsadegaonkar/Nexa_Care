"""Extend durable clinical sessions for signed Treatment Session V1 operations.

Revision ID: 20260917_treatment_session_operations
Revises: 20260916_clinical_access_sessions
Create Date: 2026-09-17 01:55:00.000000
"""

from typing import Sequence, Union

from alembic import op

revision: str = "20260917_treatment_session_operations"
down_revision: Union[str, None] = "20260916_clinical_access_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALLOWED_OPERATIONS = (
    "READ_CLINICAL_HISTORY",
    "READ_DOCUMENTS",
    "CREATE_ENCOUNTER",
    "WRITE_PRESCRIPTION",
    "WRITE_DIAGNOSIS",
    "WRITE_VITALS",
    "WRITE_CLINICAL_NOTES",
    "ORDER_INVESTIGATION",
)
_ALLOWED_OPERATIONS_JSON = (
    "[" + ",".join(f'\"{value}\"' for value in _ALLOWED_OPERATIONS) + "]"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_clinical_access_session_scope",
        "clinical_access_sessions",
        type_="check",
    )
    op.drop_constraint(
        "ck_clinical_access_session_ops_v1",
        "clinical_access_sessions",
        type_="check",
    )

    op.create_check_constraint(
        "ck_clinical_access_session_scope",
        "clinical_access_sessions",
        "scope IN ('clinical','full','treatment')",
    )
    op.create_check_constraint(
        "ck_clinical_access_session_ops_v1",
        "clinical_access_sessions",
        "("
        "scope IN ('clinical','full') "
        "AND allowed_operations = '[\"READ_CLINICAL_HISTORY\"]'::jsonb"
        ") OR ("
        "scope = 'treatment' "
        "AND jsonb_typeof(allowed_operations) = 'array' "
        "AND jsonb_array_length(allowed_operations) BETWEEN 1 AND 8 "
        f"AND allowed_operations <@ '{_ALLOWED_OPERATIONS_JSON}'::jsonb"
        ")",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_clinical_access_session_ops_v1",
        "clinical_access_sessions",
        type_="check",
    )
    op.drop_constraint(
        "ck_clinical_access_session_scope",
        "clinical_access_sessions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_clinical_access_session_scope",
        "clinical_access_sessions",
        "scope IN ('clinical','full')",
    )
    op.create_check_constraint(
        "ck_clinical_access_session_ops_v1",
        "clinical_access_sessions",
        "jsonb_typeof(allowed_operations) = 'array' "
        "AND allowed_operations = '[\"READ_CLINICAL_HISTORY\"]'::jsonb",
    )
