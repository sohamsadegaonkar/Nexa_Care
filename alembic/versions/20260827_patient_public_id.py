"""Add opaque public patient identifiers for consent-bound discovery.

Purpose: allow exact provider discovery without exposing patient UUIDs.
Preconditions: database is at 20260819_patient_profile_legal.
Existing-data behavior: assigns random non-PII IDs to every patient.
Locking risk: short metadata locks plus row updates during backfill.
Rollback position: forward-only after this revision is applied; issued public
identifiers are security-relevant, durable aliases and must not be removed.
Validation query: no NULL, duplicate, or malformed public IDs.
Forward-fix strategy: preserve issued IDs; never rewrite an applied migration.
"""

from __future__ import annotations
import secrets
import sqlalchemy as sa
from alembic import op

revision = "20260827_patient_public_id"
down_revision = "20260819_patient_profile_legal"
branch_labels = None
depends_on = None


def _new_id() -> str:
    return "NC-" + secrets.token_hex(12).upper()


def upgrade() -> None:
    # ``gen_random_bytes`` is supplied by pgcrypto (unlike PostgreSQL 16's
    # built-in ``gen_random_uuid``), so the database default must not depend
    # on an unrecorded, environment-specific extension installation.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.add_column(
        "patients",
        sa.Column(
            "public_patient_id",
            sa.String(32),
            nullable=True,
            server_default=sa.text(
                "'NC-' || upper(encode(gen_random_bytes(12), 'hex'))"
            ),
        ),
        schema="public",
    )
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT patient_uuid FROM public.patients WHERE public_patient_id IS NULL"
        )
    ).fetchall()
    for (patient_uuid,) in rows:
        while True:
            candidate = _new_id()
            exists = conn.execute(
                sa.text(
                    "SELECT 1 FROM public.patients WHERE public_patient_id = :value"
                ),
                {"value": candidate},
            ).scalar()
            if not exists:
                conn.execute(
                    sa.text(
                        "UPDATE public.patients SET public_patient_id = :value WHERE patient_uuid = :patient_uuid"
                    ),
                    {"value": candidate, "patient_uuid": patient_uuid},
                )
                break
    op.create_unique_constraint(
        "uq_patients_public_patient_id",
        "patients",
        ["public_patient_id"],
        schema="public",
    )
    op.alter_column("patients", "public_patient_id", nullable=False, schema="public")


def downgrade() -> None:
    raise RuntimeError(
        "20260827_patient_public_id is forward-only: issued public patient IDs "
        "must be preserved; apply a corrective forward migration instead."
    )
