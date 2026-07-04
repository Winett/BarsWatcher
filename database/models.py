from datetime import datetime
from typing import Optional

from sqlalchemy import String, BigInteger, Boolean, Integer, Float, Text, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(String, default=None, nullable=True)

    used_osep: Mapped[bool] = mapped_column(Boolean, default=False)
    used_bars: Mapped[bool] = mapped_column(Boolean, default=False)

    bars_login: Mapped[str] = mapped_column(String, nullable=True)
    bars_password: Mapped[str] = mapped_column(String, nullable=True)

    osep_login: Mapped[str] = mapped_column(String, nullable=True)
    osep_password: Mapped[str] = mapped_column(String, nullable=True)


class GlobalConfig(Base):
    """Глобальные настройки вотчеров. Синглтон — одна строка в таблице."""
    __tablename__ = "global_config"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    poll_interval: Mapped[int] = mapped_column(Integer, default=60)
    max_poll_interval: Mapped[int] = mapped_column(Integer, default=600)
    timeout: Mapped[int] = mapped_column(Integer, default=30)
    stagger_delay: Mapped[float] = mapped_column(Float, default=2.0)
    stagger_jitter: Mapped[float] = mapped_column(Float, default=3.0)
    auto_scale_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    user_poll_mode: Mapped[str] = mapped_column(String(20), default="global")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class UserConfig(Base):
    """Персональные настройки вотчера для конкретного пользователя."""
    __tablename__ = "user_config"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    poll_interval: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    auto_scale_enabled: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    bars_show_marks: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    osep_blacklist: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )



