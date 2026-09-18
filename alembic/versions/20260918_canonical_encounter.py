"""Add canonical treatment-session Encounter authority container.

Revision ID: 20260918_canonical_encounter
Revises: 20260916_patient_external_record_import
Create Date: 2026-09-18 22:45:00.000000

Purpose:
    Materialize one canonical server-owned Encounter UUID for one qualified
    Treatment Session V1 ClinicalAccessSession. The table carries authority
    bindings only and no clinical facts.

Preconditions:
    The repository is at the single Alembic head
    20260916_patient_external_record_import.

Existing-data behavior:
    Existing clinical access sessions are not backfilled. Encounter rows are
    created only after a fresh, qualified CREATE_ENCOUNTER operation.

Locking risk:
    CREATE TABLE / constraint installation only; no table rewrite or row
    backfill is performed.

Rollback position:
    Downgrade drops only clinical_encounters. It does not rewrite
    clinical_access_sessions.encounter_id.

Validation query:
    SELECT encounter_id, clinical_session_id, patient_id, provider_id,
           hospital_id
    FROM clinical_encounters;

Forward-fix strategy:
    Correct defects with a new linear child revision; never rewrite an applied
    migration or create a sibling head.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260918_canonical_encounter"
down_revision: Union[str, None] = "20260916_patient_external_record_import"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "clinical_encounters",
        sa.Column("encounter_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "clinical_session_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hospital_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["clinical_session_id"],
            ["clinical_access_sessions.session_id"],
            name="fk_clinical_encounter_session",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["patient_id"],
            ["patients.patient_uuid"],
            name="fk_clinical_encounter_patient",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["provider_identity.id"],
            name="fk_clinical_encounter_provider",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["hospital_id"],
            ["hospital_registry.id"],
            name="fk_clinical_encounter_hospital",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("encounter_id"),
        sa.UniqueConstraint(
            "clinical_session_id",
            name="uq_clinical_encounter_session",
        ),
    )


def downgrade() -> None:
    op.drop_table("clinical_encounters")
