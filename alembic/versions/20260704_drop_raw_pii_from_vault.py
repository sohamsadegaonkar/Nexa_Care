"""Drop the legacy raw_pii JSONB column.

Revision ID: 20260704_drop_raw_pii_from_vault
Revises:
Create Date: 2026-07-04 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260704_drop_raw_pii_from_vault"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = "20260705_nexa_v1"


def upgrade() -> None:
    # The legacy raw_pii JSONB blob is being replaced by explicitly modeled,
    # encrypted PII columns (patient_name, phone, aadhaar_abha_id). Drop it
    # wherever it exists, guarding against both the model-correct location
    # (nexa_vault) and any historical schema drift.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.nexa_vault') IS NOT NULL THEN
                ALTER TABLE public.nexa_vault
                DROP COLUMN IF EXISTS raw_pii;
            END IF;

            IF to_regclass('public.nexa_clinical') IS NOT NULL THEN
                ALTER TABLE public.nexa_clinical
                DROP COLUMN IF EXISTS raw_pii;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    # Restore the column as a nullable JSONB blob when the table exists.
    op.execute(
        """
        DO $$
        BEGIN
            IF to_regclass('public.nexa_vault') IS NOT NULL
               AND NOT EXISTS (
                   SELECT 1
                   FROM information_schema.columns
                   WHERE table_schema = 'public'
                     AND table_name = 'nexa_vault'
                     AND column_name = 'raw_pii'
               ) THEN
                ALTER TABLE public.nexa_vault
                ADD COLUMN raw_pii JSONB;
            END IF;
        END
        $$;
        """
    )
