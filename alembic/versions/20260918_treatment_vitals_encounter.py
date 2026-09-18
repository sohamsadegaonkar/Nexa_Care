"""Bind Treatment Session V1 vitals to canonical Encounter.

Revision ID: 20260918_treatment_vitals_encounter
Revises: 20260918_canonical_encounter
Create Date: 2026-09-18 23:45:00.000000

Purpose:
    Add a durable canonical Encounter reference to the existing patient_vitals
    source of truth so a Treatment Session V1 WRITE_VITALS mutation can prove
    which qualified Encounter authorized the stored clinical observation.

Preconditions:
    Repository single Alembic head is 20260918_canonical_encounter.

Existing-data behavior:
    Existing and legacy vitals remain unchanged with encounter_id = NULL.
    There is no backfill and no inference of historical Encounter ownership.

Locking risk:
    Adds one nullable UUID column, one FK, and one index. No clinical row values
    are rewritten.

Rollback position:
    Downgrade removes only the Encounter FK/index/column from patient_vitals.

Validation query:
    SELECT id, patient_id, encounter_id
    FROM patient_vitals
    WHERE encounter_id IS NOT NULL;

Forward-fix strategy:
    Correct defects with a new linear child revision. Never rewrite an applied
    migration or create a sibling Alembic head.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260918_treatment_vitals_encounter"
down_revision: Union[str, None] = "20260918_canonical_encounter"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "patient_vitals",
        sa.Column("encounter_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_patient_vitals_encounter",
        "patient_vitals",
        "clinical_encounters",
        ["encounter_id"],
        ["encounter_id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_patient_vitals_encounter_id",
        "patient_vitals",
        ["encounter_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_patient_vitals_encounter_id", table_name="patient_vitals")
    op.drop_constraint(
        "fk_patient_vitals_encounter",
        "patient_vitals",
        type_="foreignkey",
    )
    op.drop_column("patient_vitals", "encounter_id")
