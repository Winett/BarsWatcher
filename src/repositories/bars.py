from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import BarsCredentials, BarsProfile, BarsSettings, BarsState

from .base import BaseStateRepository, BaseUserOwnedRepository

__all__ = [
    "BarsStateRepository",
    "BarsCredentialsRepository",
    "BarsProfileRepository",
    "BarsSettingsRepository",
]


class BarsStateRepository(BaseStateRepository[BarsState, int]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, BarsState)

    async def get_bars_states_for_check(self, current_time: datetime):
        return await self.get_states_for_check(current_time)


class BarsCredentialsRepository(BaseUserOwnedRepository[BarsCredentials, int]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, BarsCredentials)


class BarsProfileRepository(BaseUserOwnedRepository[BarsProfile, int]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, BarsProfile)


class BarsSettingsRepository(BaseUserOwnedRepository[BarsSettings, int]):
    def __init__(self, session: AsyncSession):
        super().__init__(session, BarsSettings)
