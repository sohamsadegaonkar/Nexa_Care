"""Drop the legacy raw_pii JSONB column.

Revision ID: 20260704_drop_raw_pii_from_vault
Revises:
Create Date: 2026-07-04 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260704_drop_raw_pii_from_vault"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # The legacy raw_pii JSONB blob is being replaced by explicitly modeled,
    # encrypted PII columns (patient_name, phone, aadhaar_abha_id). Drop it
    # wherever it exists, guarding against both the model-correct location
    # (nexa_vault) and any historical schema drift.
    op.execute("ALTER TABLE nexa_vault DROP COLUMN IF EXISTS raw_pii")
    op.execute("ALTER TABLE nexa_clinical DROP COLUMN IF EXISTS raw_pii")


def downgrade() -> None:
    # Restore the column as a nullable JSONB blob.
    op.add_column(
        "nexa_vault",
        sa.Column("raw_pii", postgresql.JSONB(), nullable=True),
    )
