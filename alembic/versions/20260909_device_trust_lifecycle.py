"""Add versioned patient-device trust lifecycle and global key ownership.

Revision ID: 20260909_device_trust_lifecycle
Revises: 20260906_verification_scheduler
Create Date: 2026-09-09 01:00:00.000000

Existing enrolled keys become version 1 of a logical device whose server-owned
``device_id`` equals the existing row id. Existing public keys were accepted by
the application only after DER P-256 parsing; their stored DER bytes are hashed
for the immutable fingerprint backfill. New writes canonicalize SubjectPublicKeyInfo
DER before persistence and fingerprinting.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260909_device_trust_lifecycle"
down_revision: Union[str, None] = "20260906_verification_scheduler"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.add_column(
        "patient_device_keys",
        sa.Column("device_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "patient_device_keys",
        sa.Column("key_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "patient_device_keys",
        sa.Column("public_key_fingerprint", sa.String(64), nullable=True),
    )
    op.add_column(
        "patient_device_keys",
        sa.Column("revocation_reason_code", sa.String(64), nullable=True),
    )
    op.add_column(
        "patient_device_keys",
        sa.Column("revocation_actor", sa.String(32), nullable=True),
    )
    op.add_column(
        "patient_device_keys",
        sa.Column("replaces_key_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "patient_device_keys",
        sa.Column("replaced_by_key_id", postgresql.UUID(as_uuid=True), nullable=True),
    )

    op.execute(
        """
        UPDATE patient_device_keys
        SET device_id = id,
            key_version = 1,
            public_key_fingerprint = encode(digest(device_public_key, 'sha256'), 'hex')
        WHERE device_id IS NULL
           OR key_version IS NULL
           OR public_key_fingerprint IS NULL
        """
    )

    op.alter_column("patient_device_keys", "device_id", nullable=False)
    op.alter_column("patient_device_keys", "key_version", nullable=False)
    op.alter_column("patient_device_keys", "public_key_fingerprint", nullable=False)

    op.create_foreign_key(
        "fk_patient_device_keys_replaces_key",
        "patient_device_keys",
        "patient_device_keys",
        ["replaces_key_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_patient_device_keys_replaced_by_key",
        "patient_device_keys",
        "patient_device_keys",
        ["replaced_by_key_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_check_constraint(
        "ck_patient_device_key_version_positive",
        "patient_device_keys",
        "key_version >= 1",
    )
    op.create_check_constraint(
        "ck_patient_device_key_status",
        "patient_device_keys",
        "status IN ('active', 'revoked', 'replaced', 'compromised')",
    )
    op.create_check_constraint(
        "ck_patient_device_key_terminal_revoked_at",
        "patient_device_keys",
        "(status = 'active' AND revoked_at IS NULL) OR "
        "(status IN ('revoked', 'replaced', 'compromised') AND revoked_at IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_patient_device_key_fingerprint_format",
        "patient_device_keys",
        "public_key_fingerprint ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_patient_device_key_lineage_not_self",
        "patient_device_keys",
        "(replaces_key_id IS NULL OR replaces_key_id <> id) AND "
        "(replaced_by_key_id IS NULL OR replaced_by_key_id <> id)",
    )

    op.create_index(
        "uq_patient_device_key_fingerprint_global",
        "patient_device_keys",
        ["public_key_fingerprint"],
        unique=True,
    )
    op.create_index(
        "uq_patient_device_key_device_version",
        "patient_device_keys",
        ["device_id", "key_version"],
        unique=True,
    )
    op.create_index(
        "uq_patient_device_key_one_active_version",
        "patient_device_keys",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_patient_device_key_patient_status",
        "patient_device_keys",
        ["patient_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_patient_device_key_patient_status", table_name="patient_device_keys"
    )
    op.drop_index(
        "uq_patient_device_key_one_active_version", table_name="patient_device_keys"
    )
    op.drop_index(
        "uq_patient_device_key_device_version", table_name="patient_device_keys"
    )
    op.drop_index(
        "uq_patient_device_key_fingerprint_global", table_name="patient_device_keys"
    )

    op.drop_constraint(
        "ck_patient_device_key_lineage_not_self",
        "patient_device_keys",
        type_="check",
    )
    op.drop_constraint(
        "ck_patient_device_key_fingerprint_format",
        "patient_device_keys",
        type_="check",
    )
    op.drop_constraint(
        "ck_patient_device_key_terminal_revoked_at",
        "patient_device_keys",
        type_="check",
    )
    op.drop_constraint(
        "ck_patient_device_key_status", "patient_device_keys", type_="check"
    )
    op.drop_constraint(
        "ck_patient_device_key_version_positive",
        "patient_device_keys",
        type_="check",
    )
    op.drop_constraint(
        "fk_patient_device_keys_replaced_by_key",
        "patient_device_keys",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_patient_device_keys_replaces_key",
        "patient_device_keys",
        type_="foreignkey",
    )

    op.drop_column("patient_device_keys", "replaced_by_key_id")
    op.drop_column("patient_device_keys", "replaces_key_id")
    op.drop_column("patient_device_keys", "revocation_actor")
    op.drop_column("patient_device_keys", "revocation_reason_code")
    op.drop_column("patient_device_keys", "public_key_fingerprint")
    op.drop_column("patient_device_keys", "key_version")
    op.drop_column("patient_device_keys", "device_id")
