from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.models import GlobalSettings, UserOverride

__all__ = ["ConfigRepository"]


class ConfigRepository:
    """Глобальные настройки + персональные переопределения."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_global(self) -> GlobalSettings | None:
        stmt = select(GlobalSettings).limit(1)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_or_create_global(self) -> GlobalSettings:
        """Синглтон глобальных настроек: создаёт строку при первом обращении."""
        row = await self.get_global()
        if row is None:
            row = GlobalSettings()
            self.session.add(row)
            await self.session.flush()
        return row

    async def get_override(self, user_id: int) -> UserOverride | None:
        stmt = select(UserOverride).where(UserOverride.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
