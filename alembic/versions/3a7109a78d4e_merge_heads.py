"""merge_heads

Revision ID: 3a7109a78d4e
Revises: 20260704_drop_raw_pii_from_vault, 20260705_nexa_v1
Create Date: 2026-07-05 20:41:40.123456

"""
from typing import Sequence, Union



# revision identifiers, used by Alembic.
revision: str = '3a7109a78d4e'
down_revision: Union[str, None] = ('20260704_drop_raw_pii_from_vault', '20260705_nexa_v1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
