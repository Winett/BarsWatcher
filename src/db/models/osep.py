from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .custom_types import EncryptedString
from .enums import WatchStates
from .mixin import TimestampMixin

__all__ = ["OsepState", "OsepSettings", "OsepCredentials"]


class OsepCredentials(Base, TimestampMixin):
    __tablename__ = "osep_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    login: Mapped[str | None] = mapped_column(String, nullable=True)
    password: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)


class OsepState(Base, TimestampMixin):
    __tablename__ = "osep_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    status: Mapped[WatchStates] = mapped_column(default=WatchStates.INITIAL)
    cookies: Mapped[dict | None] = mapped_column(JSON, nullable=True)

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


class OsepSettings(Base, TimestampMixin):
    __tablename__ = "osep_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    # чёрный список отправителей (wildcard-паттерны), от которых не присылать письма
    blacklist: Mapped[list | None] = mapped_column(JSON, nullable=True)
