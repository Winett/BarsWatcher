"""pending_2fa для bars_states

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-25
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "bars_states",
        sa.Column("pending_2fa", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bars_states", "pending_2fa")
