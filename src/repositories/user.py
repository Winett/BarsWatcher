from collections.abc import Sequence
from typing import TypedDict, Unpack

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.db.models import User

from .base import BaseRepository


class UserLoadOptions(TypedDict, total=False):
    with_bars_credentials: bool
    with_bars_profile: bool
    with_bars_state: bool
    with_bars_settings: bool
    with_osep_credentials: bool
    with_osep_state: bool
    with_osep_settings: bool
    with_admin: bool


class UserRepository(BaseRepository[User, int]):

    def __init__(self, session: AsyncSession):
        super().__init__(session, User)

    @staticmethod
    def _apply_load_options(stmt: Select, **options: Unpack[UserLoadOptions]) -> Select:
        if options.get("with_admin"):
            stmt = stmt.options(selectinload(User.admin_record))
        if options.get("with_bars_credentials"):
            stmt = stmt.options(selectinload(User.bars_credentials))
        if options.get("with_bars_profile"):
            stmt = stmt.options(selectinload(User.bars_profile))
        if options.get("with_bars_state"):
            stmt = stmt.options(selectinload(User.bars_state))
        if options.get("with_bars_settings"):
            stmt = stmt.options(selectinload(User.bars_settings))
        if options.get("with_osep_credentials"):
            stmt = stmt.options(selectinload(User.osep_credentials))
        if options.get("with_osep_state"):
            stmt = stmt.options(selectinload(User.osep_state))
        if options.get("with_osep_settings"):
            stmt = stmt.options(selectinload(User.osep_settings))
        return stmt

    async def _fetch_one(
        self, stmt: Select, **options: Unpack[UserLoadOptions]
    ) -> User | None:
        stmt = self._apply_load_options(stmt, **options)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _fetch_all(
        self, stmt: Select, **options: Unpack[UserLoadOptions]
    ) -> Sequence[User]:
        stmt = self._apply_load_options(stmt, **options)
        results = await self.session.execute(stmt)
        return results.scalars().all()

    async def get_by_id(
        self, id: int, **options: Unpack[UserLoadOptions]
    ) -> User | None:
        stmt = select(User).where(User.id == id)
        return await self._fetch_one(stmt, **options)

    async def get_by_telegram_id(
        self, telegram_id: int, **options: Unpack[UserLoadOptions]
    ) -> User | None:
        stmt = select(self.model).where(self.model.telegram_id == telegram_id)
        return await self._fetch_one(stmt, **options)

    async def get_active_users(
        self, **options: Unpack[UserLoadOptions]
    ) -> Sequence[User]:
        stmt = select(self.model).where(self.model.is_active)
        return await self._fetch_all(stmt, **options)
