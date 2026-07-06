"""add_assurance_to_consent_grant_log

Revision ID: 11c1c7e3c464
Revises: 3a7109a78d4e
Create Date: 2026-07-05 20:42:45.142166

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '11c1c7e3c464'
down_revision: Union[str, None] = '3a7109a78d4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('consent_grant_log', sa.Column('assurance_level', sa.String(length=32), nullable=True))
    op.add_column('consent_grant_log', sa.Column('assurance_verified_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('consent_grant_log', 'assurance_verified_at')
    op.drop_column('consent_grant_log', 'assurance_level')
