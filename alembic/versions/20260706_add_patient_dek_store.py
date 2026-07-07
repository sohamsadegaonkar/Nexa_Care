"""add patient dek store

Revision ID: 20260706_add_patient_dek_store
Revises: d2f75cf736b2
Create Date: 2026-07-06 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '20260706_add_patient_dek_store'
down_revision: Union[str, None] = 'd2f75cf736b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'patient_dek_store',
        sa.Column('id', postgresql.UUID(as_uuid=True), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('patient_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('wrapped_dek', sa.LargeBinary(), nullable=False),
        sa.Column('dek_iv', sa.LargeBinary(), nullable=False),
        sa.Column('dek_version', sa.Integer(), server_default='1', nullable=False),
        sa.Column('algorithm', sa.String(length=32), server_default='AES-256-GCM', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('destroyed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('patient_id')
    )
    op.create_index('ix_patient_dek_store_patient_id', 'patient_dek_store', ['patient_id'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_patient_dek_store_patient_id', table_name='patient_dek_store')
    op.drop_table('patient_dek_store')
