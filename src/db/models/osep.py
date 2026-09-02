from datetime import datetime

from sqlalchemy import Integer, String, ForeignKey, JSON, DateTime, Boolean
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base
from .enums import WatchStates
from .mixin import TimestampMixin
from .custom_types import EncryptedString

__all__ = ["OsepState", "OsepSettings", "OsepCredentials"]


class OsepCredentials(Base, TimestampMixin):
    __tablename__ = "osep_credentials"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    login: Mapped[str | None] = mapped_column(String, nullable=True)
    password: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)


class OsepState(Base, TimestampMixin):
    __tablename__ = "osep_states"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[WatchStates] = mapped_column(default=WatchStates.INITIAL)
    cookies: Mapped[dict] = mapped_column(JSON)
    check_interval_minutes: Mapped[int] = mapped_column(default=3)
    next_check: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    last_error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    last_error_datetime: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )


class OsepSettings(Base, TimestampMixin):
    __tablename__ = "osep_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    hidden_mark: Mapped[bool] = mapped_column(Boolean, default=False)
