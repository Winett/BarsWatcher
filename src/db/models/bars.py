from datetime import datetime

from sqlalchemy import Integer, String, Boolean, ForeignKey, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base
from .mixin import TimestampMixin
from .custom_types import EncryptedString
from .enums import WatchStates

__all__ = ["BarsSettings", "BarsCredentials", "BarsState", "BarsProfile"]


class BarsCredentials(Base, TimestampMixin):
    __tablename__ = "bars_credentials"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    login: Mapped[str | None] = mapped_column(String, nullable=True)
    password: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)


class BarsProfile(Base, TimestampMixin):
    __tablename__ = "bars_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    fio: Mapped[str | None] = mapped_column(String, nullable=True)
    group: Mapped[str | None] = mapped_column(String, nullable=True)
    is_group_leader: Mapped[bool] = mapped_column(Boolean, default=False)


class BarsState(Base, TimestampMixin):
    __tablename__ = "bars_states"
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


class BarsSettings(Base, TimestampMixin):
    __tablename__ = "bars_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    hidden_mark: Mapped[bool] = mapped_column(Boolean, default=False)
