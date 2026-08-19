"""Widen nexa_vault encrypted PII columns to TEXT.

Revision ID: 20260819_widen_vault_pii_columns
Revises: 20260818_async_provider_jobs

Purpose: Widen ``nexa_vault.patient_name``, ``nexa_vault.phone``, and
``nexa_vault.aadhaar_abha_id`` from historical bounded VARCHAR columns
(VARCHAR(255), VARCHAR(32), VARCHAR(64)) to TEXT.
Preconditions: The database is at ``20260818_async_provider_jobs`` and ``nexa_vault``
exists with its known schema.
Existing-data behavior: Existing encrypted ciphertext values are preserved exactly
without rewrite or re-encryption.
Locking risk: ALTER TABLE ... ALTER COLUMN TYPE takes an ACCESS EXCLUSIVE lock
on ``nexa_vault``. PostgreSQL can perform this compatible VARCHAR-to-TEXT
conversion without a table rewrite, but the lock can still block readers and
writers for the DDL duration.
Rollback behavior: Downgrade checks whether any ciphertext exceeds historical VARCHAR
limits; if so, downgrade fails closed (DOWNGRADE_BLOCKED) to prevent silent truncation.
Otherwise, it safely restores VARCHAR types.
Validation approach: Static ORM/migration parity checks plus disposable real PostgreSQL
upgrade, existing ciphertext preservation, downgrade, and re-upgrade probes.
Forward-fix approach: Repair any discovered issue with a new forward-only revision;
never edit an applied parent or stamp around a failed migration.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260819_widen_vault_pii_columns"
down_revision = "20260818_async_provider_jobs"
branch_labels = None
depends_on = None


_TABLE = "public.nexa_vault"
_DOWNGRADE_ERROR = (
    "VAULT_WIDEN_DOWNGRADE_BLOCKED: ciphertext length exceeds historical varchar limits"
)


def _require_safe_downgrade(bind) -> None:
    violating_rows = bind.execute(
        sa.text(
            f"""
            SELECT count(*)
            FROM {_TABLE}
            WHERE (patient_name IS NOT NULL AND length(patient_name) > 255)
               OR (phone IS NOT NULL AND length(phone) > 32)
               OR (aadhaar_abha_id IS NOT NULL AND length(aadhaar_abha_id) > 64)
            """
        )
    ).scalar_one()
    if violating_rows > 0:
        raise RuntimeError(_DOWNGRADE_ERROR)


def upgrade() -> None:
    op.alter_column(
        "nexa_vault",
        "patient_name",
        type_=sa.Text(),
        existing_type=sa.String(255),
        existing_nullable=True,
    )
    op.alter_column(
        "nexa_vault",
        "phone",
        type_=sa.Text(),
        existing_type=sa.String(32),
        existing_nullable=True,
    )
    op.alter_column(
        "nexa_vault",
        "aadhaar_abha_id",
        type_=sa.Text(),
        existing_type=sa.String(64),
        existing_nullable=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    _require_safe_downgrade(bind)
    op.alter_column(
        "nexa_vault",
        "patient_name",
        type_=sa.String(255),
        existing_type=sa.Text(),
        existing_nullable=True,
    )
    op.alter_column(
        "nexa_vault",
        "phone",
        type_=sa.String(32),
        existing_type=sa.Text(),
        existing_nullable=True,
    )
    op.alter_column(
        "nexa_vault",
        "aadhaar_abha_id",
        type_=sa.String(64),
        existing_type=sa.Text(),
        existing_nullable=True,
    )
