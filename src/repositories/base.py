from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import TypeVar, Generic, Type, Sequence

ModelType = TypeVar("ModelType")
IdType = TypeVar("IdType", int, str, UUID)

__all__ = ["BaseRepository", "BaseStateRepository"]


class BaseRepository(Generic[ModelType, IdType]):
    def __init__(self, session: AsyncSession, model: Type[ModelType]):
        self.session = session
        self.model = model

    async def get_by_id(self, id: IdType) -> ModelType | None:
        stmt = select(self.model).where(self.model.id == id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, model: ModelType) -> None:
        self.session.add(model)

    async def delete(self, model: ModelType) -> None:
        await self.session.delete(model)


from db.models import OsepState, BarsState, WatchStates

StateModel = TypeVar("StateModel", OsepState, BarsState)


class BaseStateRepository(BaseRepository[StateModel, IdType]):
    async def _get_states_for_check(
        self, current_time: datetime
    ) -> Sequence[StateModel]:
        stmt = select(self.model).where(
            (self.model.next_check <= current_time) | (self.model.next_check.is_(None)),
            self.model.status == WatchStates.ACTIVE,
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
