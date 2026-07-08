"""Добавлено поле bars_show_marks

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2025-07-04 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('global_config', sa.Column('bars_show_marks', sa.Boolean(), server_default='true', nullable=False))
    op.add_column('user_config', sa.Column('bars_show_marks', sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column('user_config', 'bars_show_marks')
    op.drop_column('global_config', 'bars_show_marks')
