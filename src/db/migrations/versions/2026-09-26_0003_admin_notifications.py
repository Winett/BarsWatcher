"""настройки уведомлений админам в global_settings

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "global_settings",
        sa.Column("notify_service_status", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "global_settings",
        sa.Column("notify_new_users", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )


def downgrade() -> None:
    op.drop_column("global_settings", "notify_new_users")
    op.drop_column("global_settings", "notify_service_status")
