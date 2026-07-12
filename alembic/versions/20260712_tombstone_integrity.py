"""add tombstone integrity constraints

Revision ID: 20260712_tombstone_integrity
Revises: 20260707_corrections
Create Date: 2026-07-12 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260712_tombstone_integrity"
down_revision: Union[str, None] = "20260707_corrections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    duplicates = conn.execute(sa.text(
        """
        SELECT old_patient_uuid
        FROM patient_tombstones
        GROUP BY old_patient_uuid
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    )).fetchall()
    if duplicates:
        raise RuntimeError(
            "Cannot add uq_patient_tombstones_old_patient_uuid: duplicate "
            "patient_tombstones.old_patient_uuid rows exist and must be reconciled manually."
        )

    op.create_unique_constraint(
        "uq_patient_tombstones_old_patient_uuid",
        "patient_tombstones",
        ["old_patient_uuid"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_patient_tombstones_old_patient_uuid",
        "patient_tombstones",
        type_="unique",
    )
