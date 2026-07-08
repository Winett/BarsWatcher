"""Удалено bars_show_marks из global_config

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2025-07-04 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column('global_config', 'bars_show_marks')


def downgrade() -> None:
    op.add_column('global_config', sa.Column('bars_show_marks', sa.Boolean(), server_default='true', nullable=False))
