"""final runtime correction: UUID heads, outbox leases, durable idempotency

Revision ID: 20260720_final_runtime_fix
Revises: 20260719_security_runtime
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260720_final_runtime_fix"
down_revision: Union[str, None] = "20260719_security_runtime"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    head_type = bind.execute(
        sa.text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'audit_chain_heads'
              AND column_name = 'head_event_id'
            """
        )
    ).scalar_one()

    if head_type != "uuid":
        existing = bind.execute(sa.text("SELECT count(*) FROM public.audit_chain_heads")).scalar_one()
        if existing:
            raise RuntimeError(
                "audit_chain_heads.head_event_id is not UUID and contains rows; "
                "automatic conversion is unsafe because the prior BIGINT schema cannot store audit UUIDs"
            )
        op.alter_column(
            "audit_chain_heads",
            "head_event_id",
            schema="public",
            existing_type=sa.BigInteger(),
            type_=postgresql.UUID(as_uuid=True),
            postgresql_using="head_event_id::text::uuid",
        )

    op.create_foreign_key(
        "fk_audit_chain_heads_head_event",
        "audit_chain_heads",
        "audit_ledger",
        ["head_event_id"],
        ["audit_id"],
        source_schema="public",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.alter_column("audit_chain_heads", "healthy", new_column_name="is_healthy", schema="public")

    op.add_column("audit_outbox", sa.Column("processing_started_at", sa.DateTime(timezone=True)), schema="public")
    op.add_column("audit_outbox", sa.Column("lease_expires_at", sa.DateTime(timezone=True)), schema="public")
    op.add_column("audit_outbox", sa.Column("worker_id", sa.String(128)), schema="public")
    op.create_index(
        "ix_audit_outbox_expired_lease",
        "audit_outbox",
        ["lease_expires_at"],
        schema="public",
        postgresql_where=sa.text("status = 'processing'"),
    )
    op.create_index(
        "ix_audit_outbox_dead_letter",
        "audit_outbox",
        ["created_at"],
        schema="public",
        postgresql_where=sa.text("status = 'dead_letter'"),
    )

    op.add_column("patient_policies", sa.Column("tenant_id", sa.String(128)), schema="public")
    op.create_unique_constraint(
        "uq_patient_policies_tenant_patient",
        "patient_policies",
        ["tenant_id", "patient_uuid"],
        schema="public",
    )

    op.create_table(
        "mutation_idempotency",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_status", sa.Integer()),
        sa.Column("response_payload", postgresql.JSONB()),
        sa.Column("resulting_resource_version", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("tenant_id", "operation", "idempotency_key", name="uq_mutation_idempotency_scope_key"),
        schema="public",
    )
    op.create_index(
        "ix_mutation_idempotency_retention",
        "mutation_idempotency",
        ["retention_expires_at"],
        schema="public",
    )


def downgrade() -> None:
    op.drop_index("ix_mutation_idempotency_retention", table_name="mutation_idempotency", schema="public")
    op.drop_table("mutation_idempotency", schema="public")
    op.drop_constraint("uq_patient_policies_tenant_patient", "patient_policies", type_="unique", schema="public")
    op.drop_column("patient_policies", "tenant_id", schema="public")
    op.drop_index("ix_audit_outbox_dead_letter", table_name="audit_outbox", schema="public")
    op.drop_index("ix_audit_outbox_expired_lease", table_name="audit_outbox", schema="public")
    op.drop_column("audit_outbox", "worker_id", schema="public")
    op.drop_column("audit_outbox", "lease_expires_at", schema="public")
    op.drop_column("audit_outbox", "processing_started_at", schema="public")
    op.drop_constraint("fk_audit_chain_heads_head_event", "audit_chain_heads", type_="foreignkey", schema="public")
    op.alter_column("audit_chain_heads", "is_healthy", new_column_name="healthy", schema="public")
    op.alter_column(
        "audit_chain_heads",
        "head_event_id",
        schema="public",
        existing_type=postgresql.UUID(as_uuid=True),
        type_=sa.BigInteger(),
        postgresql_using="NULL",
    )
