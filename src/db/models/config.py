
from sqlalchemy import Boolean, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .mixin import TimestampMixin

__all__ = ["GlobalSettings", "UserOverride"]


class GlobalSettings(Base, TimestampMixin):
    """Глобальные настройки. Синглтон — одна строка в таблице."""

    __tablename__ = "global_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=180)
    max_poll_interval_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    request_timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    error_timeout_seconds: Mapped[int] = mapped_column(Integer, default=300)
    auto_scale_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # уведомления админам (админка)
    notify_service_status: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_new_users: Mapped[bool] = mapped_column(Boolean, default=True)


class UserOverride(Base, TimestampMixin):
    """Персональные переопределения: NULL в поле = взять глобальное значение."""

    __tablename__ = "user_overrides"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    poll_interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_poll_interval_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    request_timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    auto_scale_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
