from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base

__all__ = ["ServiceStatusLog"]


class ServiceStatusLog(Base):
    """Журнал доступности сервисов: строка = интервал одного статуса."""

    __tablename__ = "service_status_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(16))  # "bars" | "osep"
    is_up: Mapped[bool] = mapped_column(Boolean)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
