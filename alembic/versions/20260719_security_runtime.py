"""security runtime: transactional outbox, erasure registry, partitioned audit chain

Revision ID: 20260719_security_runtime
Revises: 20260718_security_governance

Adds, in one coordinated migration (each piece is independently reviewable):

  Defect 6 (transactional outbox):
    - audit_outbox table
    - patient_policies.version / last_idempotency_key (compare-and-swap)

  Defect 7 (truthful erasure):
    - patient_erasure_tombstones table (authoritative erasure registry)
    - patient_dek_store.wrapping_key_type / patient_wrapping_key_id

  Defect 8 (partitioned O(1) audit chain):
    - audit_chain_heads table (one row per chain_scope partition)
    - audit_ledger.sequence_number
    - drops the global UNIQUE(previous_hash) (incompatible with multiple
      partitions -- every partition needs its own GENESIS) and replaces it
      with UNIQUE(chain_scope, previous_hash) / UNIQUE(chain_scope, sequence_number)
    - backfills existing global-chain events by traversing the cryptographic
      graph (not timestamps), rejecting the migration outright if the
      existing chain is not a single well-formed line from GENESIS to one tip
"""

from __future__ import annotations

import hashlib
import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260719_security_runtime"
down_revision: Union[str, None] = "20260718_policy_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


class _BackfillError(RuntimeError):
    """Raised to abort the migration when an existing chain partition is not
    a single well-formed line from GENESIS to one tip. Never silently
    repaired -- an operator must investigate."""


def _calculate_hash(payload: dict, previous_hash: str) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((canonical + previous_hash).encode("utf-8")).hexdigest()


def _backfill_partition(bind, chain_scope: str) -> None:
    """Traverse the cryptographic graph for one chain_scope partition,
    assign sequence numbers in chain order, and write the chain-head row.

    Traversal is by previous_hash/record_hash linkage, never by timestamp.
    """

    rows = list(
        bind.execute(
            sa.text(
                """
                SELECT audit_id, previous_hash, record_hash, details, protocol_version
                FROM public.audit_ledger
                WHERE chain_scope = :chain_scope
                """
            ),
            {"chain_scope": chain_scope},
        ).mappings()
    )

    if not rows:
        return  # An empty partition migrates successfully without a head row.

    by_record_hash: dict[str, dict] = {}
    predecessor_of: dict[str, str] = {}  # record_hash -> previous_hash
    for row in rows:
        record_hash = row["record_hash"]
        if record_hash in by_record_hash:
            raise _BackfillError(f"duplicate record_hash in partition {chain_scope!r}: {record_hash}")
        by_record_hash[record_hash] = dict(row)
        predecessor_of[record_hash] = row["previous_hash"]

    genesis_candidates = [rh for rh, prev in predecessor_of.items() if prev == "GENESIS"]
    if len(genesis_candidates) != 1:
        raise _BackfillError(
            f"partition {chain_scope!r} has {len(genesis_candidates)} genesis events, expected exactly 1"
        )

    # successor map: previous_hash -> [record_hash, ...]
    successors: dict[str, list[str]] = {}
    for record_hash, previous_hash in predecessor_of.items():
        successors.setdefault(previous_hash, []).append(record_hash)
    for previous_hash, children in successors.items():
        if len(children) > 1:
            raise _BackfillError(
                f"partition {chain_scope!r} has multiple successors for hash {previous_hash!r} "
                f"(cycle or fork): {children}"
            )

    ordered: list[dict] = []
    current_hash = genesis_candidates[0]
    seen: set[str] = set()
    while True:
        if current_hash in seen:
            raise _BackfillError(f"partition {chain_scope!r} contains a cycle at {current_hash!r}")
        seen.add(current_hash)
        row = by_record_hash[current_hash]
        details = row["details"]
        if isinstance(details, str):
            details = json.loads(details)
        if not isinstance(details, dict) or _calculate_hash(details, row["previous_hash"]) != current_hash:
            raise _BackfillError(f"partition {chain_scope!r} record_hash mismatch at {current_hash!r}")
        ordered.append(row)
        next_hashes = successors.get(current_hash, [])
        if not next_hashes:
            break
        current_hash = next_hashes[0]

    if len(ordered) != len(rows):
        raise _BackfillError(
            f"partition {chain_scope!r} is disconnected: traversal reached {len(ordered)} of {len(rows)} events"
        )

    for index, row in enumerate(ordered, start=1):
        bind.execute(
            sa.text(
                "UPDATE public.audit_ledger SET sequence_number = :seq WHERE audit_id = :audit_id"
            ),
            {"seq": index, "audit_id": row["audit_id"]},
        )

    tip = ordered[-1]
    bind.execute(
        sa.text(
            """
            INSERT INTO public.audit_chain_heads
                (chain_partition, head_event_id, head_hash, sequence_number, protocol_version, healthy, updated_at)
            VALUES (:chain_partition, :head_event_id, :head_hash, :sequence_number, :protocol_version, TRUE, now())
            """
        ),
        {
            "chain_partition": chain_scope,
            "head_event_id": tip["audit_id"],
            "head_hash": tip["record_hash"],
            "sequence_number": len(ordered),
            "protocol_version": tip["protocol_version"],
        },
    )


def upgrade() -> None:
    bind = op.get_bind()

    tables = set(sa.inspect(bind).get_table_names(schema="public"))
    if "patient_policies" not in tables:
        raise RuntimeError(
            "Migration precondition failed: public.patient_policies must exist "
            "before applying 20260719_security_runtime."
        )

    # ── Defect 6: transactional outbox ──────────────────────────────────
    op.create_table(
        "audit_outbox",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("chain_partition", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(128), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("tenant_id", sa.String(128), nullable=True),
        sa.Column("patient_id", sa.String(128), nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="public",
    )
    op.create_index(
        "uq_audit_outbox_tenant_idempotency", "audit_outbox",
        ["tenant_id", "idempotency_key"], unique=True,
        postgresql_where=sa.text("tenant_id IS NOT NULL"), schema="public",
    )
    op.create_index(
        "uq_audit_outbox_global_idempotency", "audit_outbox",
        ["idempotency_key"], unique=True,
        postgresql_where=sa.text("tenant_id IS NULL"), schema="public",
    )
    op.create_index(
        "ix_audit_outbox_status_available_at", "audit_outbox",
        ["status", "available_at"], schema="public",
    )
    op.create_check_constraint(
        "ck_audit_outbox_status", "audit_outbox",
        "status IN ('pending', 'processing', 'processed', 'dead_letter')", schema="public",
    )

    op.add_column(
        "patient_policies",
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        schema="public",
    )
    op.add_column(
        "patient_policies",
        sa.Column("last_idempotency_key", sa.String(128), nullable=True),
        schema="public",
    )

    # ── Defect 7: authoritative erasure registry ────────────────────────
    op.create_table(
        "patient_erasure_tombstones",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True,
                   server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(128), nullable=True),
        sa.Column("patient_ref", sa.String(128), nullable=False),  # patient_id or non-PII stable hash
        sa.Column("status", sa.String(32), nullable=False, server_default="requested"),
        sa.Column("assurance_level", sa.String(64), nullable=False),
        sa.Column("wrapping_key_type", sa.String(16), nullable=False),
        sa.Column("patient_wrapping_key_id", sa.String(128), nullable=True),
        sa.Column("kms_state", sa.String(32), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_deletion_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_code", sa.String(64), nullable=True),
        sa.Column("operator_action_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("retry_required", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("audit_event_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="public",
    )
    op.create_index(
        "uq_patient_erasure_tombstones_ref", "patient_erasure_tombstones",
        ["patient_ref"], unique=True, schema="public",
    )
    op.create_check_constraint(
        "ck_patient_erasure_tombstones_status", "patient_erasure_tombstones",
        "status IN ('requested', 'access_blocked', 'key_disabled', 'deletion_scheduled', "
        "'destroyed', 'operator_action_required')",
        schema="public",
    )

    op.add_column(
        "patient_dek_store",
        sa.Column("wrapping_key_type", sa.String(16), nullable=False, server_default="shared"),
    )
    op.add_column(
        "patient_dek_store", sa.Column("patient_wrapping_key_id", sa.String(128), nullable=True)
    )

    # ── Defect 8: partitioned O(1) audit chain ──────────────────────────
    op.create_table(
        "audit_chain_heads",
        sa.Column("chain_partition", sa.String(64), primary_key=True),
        sa.Column("head_event_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("head_hash", sa.String(64), nullable=False),
        sa.Column("sequence_number", sa.BigInteger(), nullable=False),
        sa.Column("protocol_version", sa.Integer(), nullable=False),
        sa.Column("healthy", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        schema="public",
    )

    op.add_column("audit_ledger", sa.Column("sequence_number", sa.BigInteger(), nullable=True), schema="public")

    existing_partitions = [
        row[0]
        for row in bind.execute(
            sa.text("SELECT DISTINCT chain_scope FROM public.audit_ledger")
        ).fetchall()
    ]
    for partition in existing_partitions:
        _backfill_partition(bind, partition)

    op.drop_constraint("uq_audit_ledger_previous_hash", "audit_ledger", type_="unique", schema="public")
    op.create_unique_constraint(
        "uq_audit_ledger_scope_previous_hash", "audit_ledger", ["chain_scope", "previous_hash"], schema="public"
    )
    op.create_unique_constraint(
        "uq_audit_ledger_scope_sequence", "audit_ledger", ["chain_scope", "sequence_number"], schema="public"
    )


def downgrade() -> None:
    op.drop_constraint("uq_audit_ledger_scope_sequence", "audit_ledger", type_="unique", schema="public")
    op.drop_constraint("uq_audit_ledger_scope_previous_hash", "audit_ledger", type_="unique", schema="public")
    op.create_unique_constraint(
        "uq_audit_ledger_previous_hash", "audit_ledger", ["previous_hash"], schema="public"
    )
    op.drop_column("audit_ledger", "sequence_number", schema="public")
    op.drop_table("audit_chain_heads", schema="public")

    op.drop_column("patient_dek_store", "patient_wrapping_key_id")
    op.drop_column("patient_dek_store", "wrapping_key_type")
    op.drop_constraint("ck_patient_erasure_tombstones_status", "patient_erasure_tombstones", type_="check", schema="public")
    op.drop_index("uq_patient_erasure_tombstones_ref", table_name="patient_erasure_tombstones", schema="public")
    op.drop_table("patient_erasure_tombstones", schema="public")

    op.drop_column("patient_policies", "last_idempotency_key", schema="public")
    op.drop_column("patient_policies", "version", schema="public")
    op.drop_constraint("ck_audit_outbox_status", "audit_outbox", type_="check", schema="public")
    op.drop_index("ix_audit_outbox_status_available_at", table_name="audit_outbox", schema="public")
    op.drop_index("uq_audit_outbox_global_idempotency", table_name="audit_outbox", schema="public")
    op.drop_index("uq_audit_outbox_tenant_idempotency", table_name="audit_outbox", schema="public")
    op.drop_table("audit_outbox", schema="public")
