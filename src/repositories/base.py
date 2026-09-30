from collections.abc import Sequence
from datetime import datetime
from typing import TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import BarsState, OsepState, WatchStates

ModelType = TypeVar("ModelType")
IdType = TypeVar("IdType", int, str, UUID)

__all__ = ["BaseRepository", "BaseUserOwnedRepository", "BaseStateRepository"]


class BaseRepository[ModelType, IdType: (int, str, UUID)]:
    def __init__(self, session: AsyncSession, model: type[ModelType]):
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


StateModel = TypeVar("StateModel", OsepState, BarsState)


class BaseUserOwnedRepository(BaseRepository[ModelType, IdType]):
    """Репозиторий сущности, привязанной к пользователю 1:1."""

    async def get_by_user_id(self, user_id: int) -> ModelType | None:
        stmt = select(self.model).where(self.model.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


class BaseStateRepository(BaseUserOwnedRepository[StateModel, IdType]):
    async def get_states_for_check(
        self, current_time: datetime
    ) -> Sequence[StateModel]:
        """Активные состояния, у которых пора делать проверку."""
        stmt = select(self.model).where(
            (self.model.next_check <= current_time) | (self.model.next_check.is_(None)),
            self.model.status == WatchStates.ACTIVE,
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
