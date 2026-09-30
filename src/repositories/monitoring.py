from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import ServiceStatusLog

from .base import BaseRepository

__all__ = ["MonitoringRepository"]


class MonitoringRepository(BaseRepository):
    """Журнал доступности сервисов (интервалы up/down)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_open_interval(
        self, service: str, is_up: bool
    ) -> ServiceStatusLog | None:
        stmt = select(ServiceStatusLog).where(
            ServiceStatusLog.service == service,
            ServiceStatusLog.is_up == is_up,
            ServiceStatusLog.ended_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def open_interval(
        self, service: str, is_up: bool, started_at: datetime
    ) -> ServiceStatusLog:
        record = ServiceStatusLog(
            service=service, is_up=is_up, started_at=started_at
        )
        self.session.add(record)
        return record

    async def close_interval(self, record: ServiceStatusLog, ended_at: datetime):
        record.ended_at = ended_at
