import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from loguru import logger

from database.models import GlobalConfig, UserConfig
from watchers.models.watcher_models import WatcherConfig, BarsWatcherConfig, OsepWatcherConfig


class ConfigService:
    """Загрузка, кэширование, резолвинг конфигурации вотчеров.

    3 уровня: дефолты WatcherConfig → GlobalConfig (БД) → UserConfig (БД).
    """

    MIN_POLL_INTERVAL = 60

    def __init__(self, session_maker: sessionmaker):
        self._session_maker = session_maker
        self._global_cache: Optional[GlobalConfig] = None
        self._global_cache_time: float = 0
        self._user_cache: dict[int, tuple[UserConfig, float]] = {}
        self._cache_ttl = 300  # 5 минут

    async def _ensure_global(self) -> GlobalConfig:
        """Убедиться что в global_config есть хотя бы одна запись."""
        now = time.time()
        if self._global_cache and (now - self._global_cache_time) < self._cache_ttl:
            return self._global_cache

        async with self._session_maker() as session:
            result = await session.execute(select(GlobalConfig).limit(1))
            row = result.scalar_one_or_none()

            if row is None:
                row = GlobalConfig()
                session.add(row)
                await session.commit()
                await session.refresh(row)
                logger.info("ConfigService: создана дефолтная запись global_config")

            self._global_cache = row
            self._global_cache_time = now
            return row

    async def get_global(self) -> GlobalConfig:
        """Получить глобальный конфиг (из кэша или БД)."""
        return await self._ensure_global()

    async def get_user(self, user_id: int) -> Optional[UserConfig]:
        """Получить персональный конфиг (из кэша или БД). None если не задан."""
        now = time.time()
        if user_id in self._user_cache:
            cached, cached_time = self._user_cache[user_id]
            if (now - cached_time) < self._cache_ttl:
                return cached

        async with self._session_maker() as session:
            result = await session.execute(
                select(UserConfig).where(UserConfig.user_id == user_id)
            )
            row = result.scalar_one_or_none()

            if row:
                self._user_cache[user_id] = (row, now)

            return row

    async def resolve_config(self, user_id: int) -> WatcherConfig:
        """Собрать финальный WatcherConfig для конкретного пользователя.

        Приоритет: UserConfig → GlobalConfig → дефолт WatcherConfig.
        """
        global_cfg = await self.get_global()
        user_cfg = await self.get_user(user_id)

        poll = global_cfg.poll_interval
        if user_cfg and user_cfg.poll_interval is not None:
            poll = user_cfg.poll_interval
        poll = max(poll, self.MIN_POLL_INTERVAL)

        return WatcherConfig(
            poll_interval=poll,
            timeout=global_cfg.timeout,
        )

    async def resolve_bars_config(self, user_id: int) -> BarsWatcherConfig:
        """Собрать BarsWatcherConfig для конкретного пользователя.

        Дефолт: show_marks=True. Персональная настройка переопределяет.
        """
        user_cfg = await self.get_user(user_id)

        show_marks = True  # дефолт
        if user_cfg and user_cfg.bars_show_marks is not None:
            show_marks = user_cfg.bars_show_marks

        return BarsWatcherConfig(show_marks=show_marks)

    async def resolve_osep_config(self, user_id: int) -> OsepWatcherConfig:
        """Собрать OsepWatcherConfig для конкретного пользователя."""
        import json
        user_cfg = await self.get_user(user_id)

        blacklist = []
        if user_cfg and user_cfg.osep_blacklist:
            try:
                blacklist = json.loads(user_cfg.osep_blacklist)
            except (json.JSONDecodeError, TypeError):
                blacklist = []

        return OsepWatcherConfig(blacklist=blacklist)

    async def is_auto_scale_enabled(self, user_id: int) -> bool:
        """Проверить, включена ли авто-шкалирование для пользователя."""
        global_cfg = await self.get_global()
        user_cfg = await self.get_user(user_id)

        if user_cfg and user_cfg.auto_scale_enabled is not None:
            return user_cfg.auto_scale_enabled

        return global_cfg.auto_scale_enabled

    async def set_global(self, **kwargs) -> GlobalConfig:
        """Обновить глобальный конфиг. Сбрасывает кэш."""
        async with self._session_maker() as session:
            result = await session.execute(select(GlobalConfig).limit(1))
            row = result.scalar_one_or_none()

            if row is None:
                row = GlobalConfig()
                session.add(row)

            for key, value in kwargs.items():
                if hasattr(row, key):
                    setattr(row, key, value)

            await session.commit()
            await session.refresh(row)

            self._global_cache = row
            self._global_cache_time = time.time()
            logger.info(f"ConfigService: global_config обновлён: {kwargs}")
            return row

    async def set_user(self, user_id: int, **kwargs) -> UserConfig:
        """Обновить/создать персональный конфиг. Сбрасывает кэш."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(UserConfig).where(UserConfig.user_id == user_id)
            )
            row = result.scalar_one_or_none()

            if row is None:
                row = UserConfig(user_id=user_id)
                session.add(row)

            for key, value in kwargs.items():
                if hasattr(row, key):
                    setattr(row, key, value)

            await session.commit()
            await session.refresh(row)

            self._user_cache[user_id] = (row, time.time())
            logger.info(f"ConfigService: user_config для {user_id} обновлён: {kwargs}")
            return row

    async def reset_user(self, user_id: int) -> bool:
        """Удалить персональный конфиг (вернуться к глобальному)."""
        async with self._session_maker() as session:
            result = await session.execute(
                select(UserConfig).where(UserConfig.user_id == user_id)
            )
            row = result.scalar_one_or_none()

            if row:
                await session.delete(row)
                await session.commit()
                self._user_cache.pop(user_id, None)
                logger.info(f"ConfigService: user_config для {user_id} удалён")
                return True

            return False

    def invalidate_cache(self):
        """Сбросить все кэши."""
        self._global_cache = None
        self._global_cache_time = 0
        self._user_cache.clear()
        logger.debug("ConfigService: кэш сброшен")
