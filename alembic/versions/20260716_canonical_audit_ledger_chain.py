"""evolve the canonical audit ledger for hash-chain application events

Revision ID: 20260716_audit_ledger_chain
Revises: 20260715_patient_auth_identity
Create Date: 2026-07-16 00:00:00.000000
"""

from __future__ import annotations

import hashlib
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260716_audit_ledger_chain"
down_revision: Union[str, None] = "20260715_patient_auth_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _calculate_hash(payload: dict, previous_hash: str) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((canonical + previous_hash).encode("utf-8")).hexdigest()


def _timestamp_text(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def upgrade() -> None:
    op.add_column("audit_ledger", sa.Column("trace_id", sa.Text(), nullable=True), schema="public")
    op.add_column("audit_ledger", sa.Column("status", sa.Text(), nullable=True), schema="public")
    op.add_column("audit_ledger", sa.Column("previous_hash", sa.Text(), nullable=True), schema="public")
    op.add_column("audit_ledger", sa.Column("record_hash", sa.Text(), nullable=True), schema="public")
    op.add_column(
        "audit_ledger",
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
        schema="public",
    )

    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            """
            SELECT audit_id, actor_id, action, resource, details, timestamp
            FROM public.audit_ledger
            ORDER BY timestamp ASC, audit_id ASC
            """
        )
    ).mappings()

    previous_hash = "GENESIS"
    for row in rows:
        raw_details = row["details"]
        legacy_details = raw_details if isinstance(raw_details, dict) else {}
        trace_id = str(legacy_details.get("trace_id") or f"migration-{row['audit_id']}")
        status = str(legacy_details.get("status") or "LEGACY")
        timestamp = _timestamp_text(row["timestamp"])
        payload = {
            "trace_id": trace_id,
            "actor_uid": str(row["actor_id"]),
            "event": str(row["action"]),
            "target_id": str(row["resource"] or ""),
            "status": status,
            "timestamp": timestamp,
        }
        if raw_details is not None:
            payload["metadata"] = raw_details
        record_hash = _calculate_hash(payload, previous_hash)
        bind.execute(
            sa.text(
                """
                UPDATE public.audit_ledger
                SET trace_id = :trace_id,
                    status = :status,
                    details = CAST(:details AS JSONB),
                    previous_hash = :previous_hash,
                    record_hash = :record_hash,
                    created_at = timestamp
                WHERE audit_id = :audit_id
                """
            ),
            {
                "audit_id": row["audit_id"],
                "trace_id": trace_id,
                "status": status,
                "details": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                "previous_hash": previous_hash,
                "record_hash": record_hash,
            },
        )
        previous_hash = record_hash

    op.alter_column("audit_ledger", "trace_id", nullable=False, schema="public")
    op.alter_column("audit_ledger", "status", nullable=False, schema="public")
    op.alter_column("audit_ledger", "previous_hash", nullable=False, schema="public")
    op.alter_column("audit_ledger", "record_hash", nullable=False, schema="public")
    op.alter_column("audit_ledger", "created_at", nullable=False, schema="public")

    op.create_unique_constraint(
        "uq_audit_ledger_previous_hash",
        "audit_ledger",
        ["previous_hash"],
        schema="public",
    )
    op.create_unique_constraint(
        "uq_audit_ledger_record_hash",
        "audit_ledger",
        ["record_hash"],
        schema="public",
    )
    op.create_check_constraint(
        "ck_audit_ledger_hash_lengths",
        "audit_ledger",
        "(previous_hash = 'GENESIS' OR length(previous_hash) = 64) AND length(record_hash) = 64",
        schema="public",
    )
    op.create_index("idx_audit_ledger_created_at", "audit_ledger", ["created_at"], schema="public")
    op.create_index("idx_audit_ledger_actor", "audit_ledger", ["actor_id"], schema="public")
    op.create_index("idx_audit_ledger_action", "audit_ledger", ["action"], schema="public")
    op.create_index("idx_audit_ledger_resource", "audit_ledger", ["resource"], schema="public")


def downgrade() -> None:
    op.drop_index("idx_audit_ledger_resource", table_name="audit_ledger", schema="public")
    op.drop_index("idx_audit_ledger_action", table_name="audit_ledger", schema="public")
    op.drop_index("idx_audit_ledger_actor", table_name="audit_ledger", schema="public")
    op.drop_index("idx_audit_ledger_created_at", table_name="audit_ledger", schema="public")
    op.drop_constraint("ck_audit_ledger_hash_lengths", "audit_ledger", type_="check", schema="public")
    op.drop_constraint("uq_audit_ledger_record_hash", "audit_ledger", type_="unique", schema="public")
    op.drop_constraint("uq_audit_ledger_previous_hash", "audit_ledger", type_="unique", schema="public")
    op.drop_column("audit_ledger", "created_at", schema="public")
    op.drop_column("audit_ledger", "record_hash", schema="public")
    op.drop_column("audit_ledger", "previous_hash", schema="public")
    op.drop_column("audit_ledger", "status", schema="public")
    op.drop_column("audit_ledger", "trace_id", schema="public")
