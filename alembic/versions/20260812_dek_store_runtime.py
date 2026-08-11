"""Repair the patient DEK store schema for runtime versioning.

Revision ID: 20260812_dek_store_runtime
Revises: 20260810_identity_review

Purpose: reconcile ``patient_dek_store`` with the existing runtime contract:
multiple immutable DEK versions per patient, an explicit active flag, and a
unique ``(patient_id, dek_version)`` pair.
Preconditions: the old patient-only unique constraint and unique index must be
present with their known definitions, and no duplicate ``(patient_id,
dek_version)`` rows may exist.
Existing-data behavior: existing wrapped DEKs, IVs, versions, and metadata are
never rewritten. ``is_active`` is backfilled deterministically from
``destroyed_at`` (not destroyed means active) and then made NOT NULL.
Locking risk: the column rewrite, uniqueness changes, and index creation take
ordinary PostgreSQL DDL locks on ``patient_dek_store``; run as a controlled
forward migration.
Rollback limitation: downgrade refuses to run when a patient has more than one
version, because restoring patient-only uniqueness would destroy valid history.
Validation queries: inspect ``information_schema.columns``,
``pg_constraint``, and ``pg_indexes`` for the final column, constraint, and
index definitions.
Forward-fix policy: correct an applied migration with a later forward revision;
never stamp past a failed migration, merge rows, delete history, or renumber
versions.
"""

from alembic import op
import sqlalchemy as sa


revision = "20260812_dek_store_runtime"
down_revision = "20260810_identity_review"
branch_labels = None
depends_on = None


_TABLE = "public.patient_dek_store"
_OLD_CONSTRAINT = "patient_dek_store_patient_id_key"
_OLD_INDEX = "ix_patient_dek_store_patient_id"
_NEW_CONSTRAINT = "uq_patient_dek_version"
_NEW_INDEX = "ix_patient_dek_store_patient_id"
_DOWNGRADE_ERROR = "DEK_STORE_DOWNGRADE_BLOCKED: multiple DEK versions exist for a patient"


def _duplicate_versions(bind) -> list[tuple[object, int, int]]:
    return list(
        bind.execute(
            sa.text(
                f"""
                SELECT patient_id, dek_version, count(*)
                FROM {_TABLE}
                GROUP BY patient_id, dek_version
                HAVING count(*) > 1
                """
            )
        ).all()
    )


def _multiple_patient_versions(bind) -> list[tuple[object, int]]:
    return list(
        bind.execute(
            sa.text(
                f"""
                SELECT patient_id, count(*)
                FROM {_TABLE}
                GROUP BY patient_id
                HAVING count(*) > 1
                """
            )
        ).all()
    )


def _require_old_uniqueness(bind) -> None:
    constraint = bind.execute(
        sa.text(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'public.patient_dek_store'::regclass
              AND conname = :name
              AND contype = 'u'
            """
        ),
        {"name": _OLD_CONSTRAINT},
    ).scalar_one_or_none()
    if constraint != "UNIQUE (patient_id)":
        raise RuntimeError(
            "DEK_STORE_SCHEMA_PRECONDITION_FAILED: expected patient-only unique constraint"
        )

    index = bind.execute(
        sa.text(
            """
            SELECT i.indisunique, pg_get_indexdef(i.indexrelid)
            FROM pg_index AS i
            JOIN pg_class AS c ON c.oid = i.indexrelid
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname = :name
            """
        ),
        {"name": _OLD_INDEX},
    ).one_or_none()
    if index is None or not index[0] or index[1] != (
        "CREATE UNIQUE INDEX ix_patient_dek_store_patient_id ON public.patient_dek_store USING btree (patient_id)"
    ):
        raise RuntimeError(
            "DEK_STORE_SCHEMA_PRECONDITION_FAILED: expected unique patient index"
        )


def _require_no_duplicate_versions(bind, *, phase: str) -> None:
    if phase == "DOWNGRADE":
        if _multiple_patient_versions(bind):
            raise RuntimeError(_DOWNGRADE_ERROR)
        return
    duplicates = _duplicate_versions(bind)
    if duplicates:
        raise RuntimeError(
            f"DEK_STORE_{phase}_BLOCKED: duplicate patient/dek_version rows exist"
        )


def upgrade() -> None:
    bind = op.get_bind()
    _require_no_duplicate_versions(bind, phase="UPGRADE")
    _require_old_uniqueness(bind)

    existing_column = bind.execute(
        sa.text(
            """
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'patient_dek_store'
              AND column_name = 'is_active'
            """
        )
    ).scalar_one_or_none()
    if existing_column is not None:
        raise RuntimeError(
            "DEK_STORE_SCHEMA_PRECONDITION_FAILED: is_active already exists"
        )

    op.add_column(
        "patient_dek_store",
        sa.Column("is_active", sa.Boolean(), nullable=True),
    )
    bind.execute(
        sa.text(
            """
            UPDATE public.patient_dek_store
            SET is_active = CASE WHEN destroyed_at IS NULL THEN TRUE ELSE FALSE END
            WHERE is_active IS NULL
            """
        )
    )
    remaining_nulls = bind.execute(
        sa.text(
            "SELECT count(*) FROM public.patient_dek_store WHERE is_active IS NULL"
        )
    ).scalar_one()
    if remaining_nulls:
        raise RuntimeError(
            "DEK_STORE_SCHEMA_BACKFILL_FAILED: is_active remains NULL"
        )
    op.alter_column("patient_dek_store", "is_active", nullable=False)

    op.drop_constraint(_OLD_CONSTRAINT, "patient_dek_store", type_="unique")
    op.drop_index(_OLD_INDEX, table_name="patient_dek_store")
    op.create_unique_constraint(
        _NEW_CONSTRAINT,
        "patient_dek_store",
        ["patient_id", "dek_version"],
    )
    op.create_index(_NEW_INDEX, "patient_dek_store", ["patient_id"])


def downgrade() -> None:
    bind = op.get_bind()
    _require_no_duplicate_versions(bind, phase="DOWNGRADE")

    new_constraint = bind.execute(
        sa.text(
            """
            SELECT pg_get_constraintdef(oid)
            FROM pg_constraint
            WHERE conrelid = 'public.patient_dek_store'::regclass
              AND conname = :name
              AND contype = 'u'
            """
        ),
        {"name": _NEW_CONSTRAINT},
    ).scalar_one_or_none()
    if new_constraint != "UNIQUE (patient_id, dek_version)":
        raise RuntimeError(
            "DEK_STORE_SCHEMA_PRECONDITION_FAILED: expected version unique constraint"
        )

    op.drop_constraint(_NEW_CONSTRAINT, "patient_dek_store", type_="unique")
    op.drop_index(_NEW_INDEX, table_name="patient_dek_store")
    op.create_unique_constraint(
        _OLD_CONSTRAINT,
        "patient_dek_store",
        ["patient_id"],
    )
    op.create_index(
        _OLD_INDEX,
        "patient_dek_store",
        ["patient_id"],
        unique=True,
    )
    op.drop_column("patient_dek_store", "is_active")
