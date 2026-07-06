"""add_device_public_key_to_biometric_registry

Revision ID: d2f75cf736b2
Revises: 2a4a90c20168
Create Date: 2026-07-06 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd2f75cf736b2'
down_revision: Union[str, None] = '2a4a90c20168'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('biometric_registry', sa.Column('device_public_key', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('biometric_registry', 'device_public_key')
