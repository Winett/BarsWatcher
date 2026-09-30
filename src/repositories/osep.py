from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import OsepCredentials, OsepSettings, OsepState

from .base import BaseStateRepository, BaseUserOwnedRepository

__all__ = [
    "OsepStateRepository",
    "OsepCredentialsRepository",
    "OsepSettingsRepository",
]


class OsepStateRepository(BaseStateRepository[OsepState, int]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, OsepState)

    async def get_osep_states_for_check(self, current_time: datetime):
        return await self.get_states_for_check(current_time)


class OsepCredentialsRepository(BaseUserOwnedRepository[OsepCredentials, int]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, OsepCredentials)


class OsepSettingsRepository(BaseUserOwnedRepository[OsepSettings, int]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, OsepSettings)
