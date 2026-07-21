"""harden policy timestamps and audit partition widths

Revision ID: 20260721_policy_audit_types
Revises: 20260720_final_runtime_fix
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_policy_audit_types"
down_revision: Union[str, None] = "20260720_final_runtime_fix"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_POLICY_TIMESTAMP_PATTERN = (
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}"
    r"(?:\.\d+)?(?:Z|[+-]\d{2}(?::?\d{2})?)?$"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    required_tables = {
        "patient_policies",
        "audit_ledger",
        "audit_outbox",
        "audit_chain_heads",
    }
    missing = required_tables - set(inspector.get_table_names(schema="public"))
    if missing:
        raise RuntimeError(
            "Migration precondition failed: required public tables are missing: "
            + ", ".join(sorted(missing))
        )

    invalid_timestamp_count = bind.execute(
        sa.text(
            """
            SELECT count(*)
            FROM public.patient_policies
            WHERE updated_at IS NOT NULL
              AND btrim(updated_at) <> ''
              AND btrim(updated_at) !~ :timestamp_pattern
            """
        ),
        {"timestamp_pattern": _POLICY_TIMESTAMP_PATTERN},
    ).scalar_one()
    if invalid_timestamp_count:
        raise RuntimeError(
            "Migration precondition failed: public.patient_policies.updated_at "
            "contains values that are not ISO-8601 timestamps."
        )

    bind.execute(
        sa.text(
            """
            UPDATE public.patient_policies
            SET updated_at = now()::text
            WHERE updated_at IS NULL OR btrim(updated_at) = ''
            """
        )
    )
    op.alter_column(
        "patient_policies",
        "updated_at",
        schema="public",
        existing_type=sa.String(),
        type_=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
        postgresql_using="updated_at::timestamptz",
    )

    for table_name, column_name in (
        ("audit_ledger", "chain_scope"),
        ("audit_outbox", "chain_partition"),
        ("audit_chain_heads", "chain_partition"),
    ):
        op.alter_column(
            table_name,
            column_name,
            schema="public",
            existing_type=sa.String(64),
            type_=sa.String(192),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    overlong = bind.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM public.audit_ledger WHERE length(chain_scope) > 64
                UNION ALL
                SELECT 1 FROM public.audit_outbox WHERE length(chain_partition) > 64
                UNION ALL
                SELECT 1 FROM public.audit_chain_heads WHERE length(chain_partition) > 64
            )
            """
        )
    ).scalar_one()
    if overlong:
        raise RuntimeError(
            "Downgrade precondition failed: audit partitions longer than 64 characters exist."
        )

    for table_name, column_name in (
        ("audit_chain_heads", "chain_partition"),
        ("audit_outbox", "chain_partition"),
        ("audit_ledger", "chain_scope"),
    ):
        op.alter_column(
            table_name,
            column_name,
            schema="public",
            existing_type=sa.String(192),
            type_=sa.String(64),
            existing_nullable=False,
        )

    op.alter_column(
        "patient_policies",
        "updated_at",
        schema="public",
        existing_type=sa.DateTime(timezone=True),
        type_=sa.String(),
        nullable=True,
        server_default=None,
        postgresql_using="updated_at::text",
    )
