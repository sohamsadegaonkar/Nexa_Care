"""add patient device key timestamps

Revision ID: 20260713_device_key_timestamps
Revises: 20260712_tombstone_integrity
Create Date: 2026-07-13 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "20260713_device_key_timestamps"
down_revision: Union[str, None] = "20260712_tombstone_integrity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("patient_device_keys")}
    if "created_at" not in columns:
        op.add_column(
            "patient_device_keys",
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
    if "updated_at" not in columns:
        op.add_column(
            "patient_device_keys",
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )


def downgrade() -> None:
    # Non-destructive: these audit columns may predate this correction and
    # may contain real metadata, so their provenance cannot be inferred.
    pass
