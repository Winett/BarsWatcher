from datetime import datetime, time

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .custom_types import EncryptedString
from .enums import WatchStates
from .mixin import TimestampMixin

__all__ = ["BarsSettings", "BarsCredentials", "BarsState", "BarsProfile"]


class BarsCredentials(Base, TimestampMixin):
    __tablename__ = "bars_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    login: Mapped[str | None] = mapped_column(String, nullable=True)
    password: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)


class BarsProfile(Base, TimestampMixin):
    __tablename__ = "bars_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    fio: Mapped[str | None] = mapped_column(String, nullable=True)
    group: Mapped[str | None] = mapped_column(String, nullable=True)
    # ответственный за журнал (староста/зам) — определяется сравнением логина
    # пользователя с ответственными за журнал в его группе
    is_journal_responsible: Mapped[bool] = mapped_column(Boolean, default=False)


class BarsState(Base, TimestampMixin):
    __tablename__ = "bars_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[WatchStates] = mapped_column(default=WatchStates.INITIAL)
    cookies: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # ожидающее 2FA: {"token", "tid", "account"} — переживает перезапуск worker
    pending_2fa: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # текущий интервал опроса (авто-масштабирование); None = из настроек
    current_interval_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    next_check: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error_datetime: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BarsSettings(Base, TimestampMixin):
    __tablename__ = "bars_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    # скрывать значение оценки в уведомлении
    hidden_mark: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_marks: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_absences: Mapped[bool] = mapped_column(Boolean, default=True)
    # напоминания о проставлении пар (только для ответственного за журнал)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    reminder_time: Mapped[time | None] = mapped_column(Time, nullable=True)  # v2
    reminder_weekdays: Mapped[list | None] = mapped_column(JSON, nullable=True)  # v2
