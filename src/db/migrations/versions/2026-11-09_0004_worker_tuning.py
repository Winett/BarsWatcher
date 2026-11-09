"""лимит параллельных проверок + устойчивые счётчики папок ОСЭП

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-09

global_settings.worker_max_jobs — переопределение лимита воркеров из админки
(NULL = значение WORKER_MAX_JOBS из .env);
osep_states.folder_counts — счётчики папок для diff: теперь в БД, чтобы
перезапуск Redis не приводил к повторной «базе» и потере уведомлений.
"""

import sqlalchemy as sa

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "global_settings",
        sa.Column("worker_max_jobs", sa.Integer(), nullable=True),
    )
    op.add_column(
        "osep_states",
        sa.Column("folder_counts", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("osep_states", "folder_counts")
    op.drop_column("global_settings", "worker_max_jobs")
