"""начальная схема ядра

Revision ID: 0001
Revises:
Create Date: 2026-09-24

Контексты: users, bars, osep, config (global_settings/user_overrides),
monitoring (service_status_logs).
"""

import sqlalchemy as sa

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

watch_states = sa.Enum(
    "INITIAL",
    "ACTIVE",
    "INACTIVE",
    "WAITING_2FA",
    "INVALID_CREDS",
    name="watchstates",
)


def _timestamps() -> list:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    # ── user ─────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False, unique=True),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
    )
    op.create_table(
        "admins",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        *_timestamps(),
    )

    # ── bars ────────────────────────────────────────────────────────────
    op.create_table(
        "bars_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("login", sa.String(), nullable=True),
        sa.Column("password", sa.String(), nullable=True),
        *_timestamps(),
    )
    op.create_table(
        "bars_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("fio", sa.String(), nullable=True),
        sa.Column("group", sa.String(), nullable=True),
        sa.Column(
            "is_journal_responsible",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        *_timestamps(),
    )
    op.create_table(
        "bars_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", watch_states, nullable=False, server_default="INITIAL"),
        sa.Column("cookies", sa.JSON(), nullable=True),
        sa.Column("current_interval_seconds", sa.Integer(), nullable=True),
        sa.Column("next_check", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_message", sa.String(), nullable=True),
        sa.Column("last_error_datetime", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_table(
        "bars_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("hidden_mark", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("notify_marks", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "notify_absences", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "reminders_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("reminder_time", sa.Time(), nullable=True),
        sa.Column("reminder_weekdays", sa.JSON(), nullable=True),
        *_timestamps(),
    )

    # ── osep ────────────────────────────────────────────────────────────
    op.create_table(
        "osep_credentials",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("login", sa.String(), nullable=True),
        sa.Column("password", sa.String(), nullable=True),
        *_timestamps(),
    )
    op.create_table(
        "osep_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", watch_states, nullable=False, server_default="INITIAL"),
        sa.Column("cookies", sa.JSON(), nullable=True),
        sa.Column("current_interval_seconds", sa.Integer(), nullable=True),
        sa.Column("next_check", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_message", sa.String(), nullable=True),
        sa.Column("last_error_datetime", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_table(
        "osep_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("blacklist", sa.JSON(), nullable=True),
        *_timestamps(),
    )

    # ── config ──────────────────────────────────────────────────────────
    op.create_table(
        "global_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "poll_interval_seconds", sa.Integer(), nullable=False, server_default="180"
        ),
        sa.Column(
            "max_poll_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default="1800",
        ),
        sa.Column(
            "request_timeout_seconds", sa.Integer(), nullable=False, server_default="30"
        ),
        sa.Column(
            "error_timeout_seconds", sa.Integer(), nullable=False, server_default="300"
        ),
        sa.Column(
            "auto_scale_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        *_timestamps(),
    )
    op.create_table(
        "user_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("poll_interval_seconds", sa.Integer(), nullable=True),
        sa.Column("max_poll_interval_seconds", sa.Integer(), nullable=True),
        sa.Column("request_timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("error_timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("auto_scale_enabled", sa.Boolean(), nullable=True),
        *_timestamps(),
    )

    # ── monitoring ──────────────────────────────────────────────────────
    op.create_table(
        "service_status_logs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("service", sa.String(length=16), nullable=False),
        sa.Column("is_up", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("service_status_logs")
    op.drop_table("user_overrides")
    op.drop_table("global_settings")
    op.drop_table("osep_settings")
    op.drop_table("osep_states")
    op.drop_table("osep_credentials")
    op.drop_table("bars_settings")
    op.drop_table("bars_states")
    op.drop_table("bars_profiles")
    op.drop_table("bars_credentials")
    op.drop_table("admins")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS watchstates")
