"""add patient push tokens table

Revision ID: 20260706_add_push_tokens
Revises: 11c1c7e3c464
Create Date: 2026-07-06 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20260706_add_push_tokens'
down_revision: Union[str, None] = '11c1c7e3c464'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'patient_push_tokens',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('patient_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('expo_push_token', sa.String(length=255), nullable=False),
        sa.Column('platform', sa.String(length=20), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('patient_id', 'expo_push_token', name='uq_patient_push_token')
    )
    op.create_index('ix_patient_push_tokens_patient_id', 'patient_push_tokens', ['patient_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_patient_push_tokens_patient_id', table_name='patient_push_tokens')
    op.drop_table('patient_push_tokens')
