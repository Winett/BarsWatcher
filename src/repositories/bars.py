from sqlalchemy.ext.asyncio import AsyncSession

from datetime import datetime
from .base import BaseStateRepository
from db.models import BarsState

__all__ = ["BarsStateRepository"]


class BarsStateRepository(BaseStateRepository[BarsState, int]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, BarsState)

    async def get_bars_states_for_check(self, current_time: datetime):
        return await self._get_states_for_check(current_time)
