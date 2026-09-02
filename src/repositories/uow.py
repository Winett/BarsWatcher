from sqlalchemy.ext.asyncio import AsyncSession

from .user import UserRepository
from .bars import BarsStateRepository
from .osep import OsepStateRepository


class UnitOfWork:
    def __init__(self, session: AsyncSession):
        self.session = session

        self.users = UserRepository(session)
        self.bars = BarsStateRepository(session)
        self.osep = OsepStateRepository(session)

    async def commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            await self.rollback()
