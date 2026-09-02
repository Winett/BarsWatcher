from sqlalchemy.ext.asyncio import AsyncSession

from datetime import datetime
from .base import BaseStateRepository
from db.models import OsepState

__all__ = ["OsepStateRepository"]


class OsepStateRepository(BaseStateRepository[OsepState, int]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, OsepState)

    async def get_osep_states_for_check(self, current_time: datetime):
        return await self._get_states_for_check(current_time)
