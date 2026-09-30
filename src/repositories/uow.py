from sqlalchemy.ext.asyncio import AsyncSession

from .bars import (
    BarsCredentialsRepository,
    BarsProfileRepository,
    BarsSettingsRepository,
    BarsStateRepository,
)
from .config import ConfigRepository
from .monitoring import MonitoringRepository
from .osep import (
    OsepCredentialsRepository,
    OsepSettingsRepository,
    OsepStateRepository,
)
from .user import UserRepository


class UnitOfWork:
    def __init__(self, session: AsyncSession):
        self.session = session

        self.users = UserRepository(session)

        self.bars_state = BarsStateRepository(session)
        self.bars_credentials = BarsCredentialsRepository(session)
        self.bars_profile = BarsProfileRepository(session)
        self.bars_settings = BarsSettingsRepository(session)

        self.osep_state = OsepStateRepository(session)
        self.osep_credentials = OsepCredentialsRepository(session)
        self.osep_settings = OsepSettingsRepository(session)

        self.config = ConfigRepository(session)
        self.monitoring = MonitoringRepository(session)

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            await self.rollback()
