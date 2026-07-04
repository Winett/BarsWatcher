from loguru import logger
import asyncio
from aiogram import Bot

from database.db import async_session
from services.user import UserService
from settings import WORKDIR
from watchers.models.watcher_models import UserCredentials, WatcherType
from watchers.services.cache_service import AsyncFileCacher
from watchers.services.watcher_factory import WatcherFactory
from watchers.session.pool_session import PoolSession
from watchers.core.exceptions import Auth2FA


async def recover_notifications_over_restarting_bot(bot: Bot):
    async with async_session() as session:
        user_service = UserService(session)
        users = await user_service.find_all_users_used_bars()

    cache = AsyncFileCacher(filename=WORKDIR / "cache.json")
    logger.info(f"Всего на перезапуск БАРС - {len(users)}")
    for i, user in enumerate(users, start=1):
        logger.info(f"Перезапускаю BarsWatcher после рестарта бота для {user.user_id} {user.bars_login} ({i}/{len(users)})")

        # Создаём auth и логинимся
        auth, session_obj = WatcherFactory.create_auth_and_session(
            user_id=user.user_id,
            service="bars",
            login=user.bars_login,
            password=user.bars_password,
            watcher_type=WatcherType.BARS
        )

        try:
            res = await auth.login()
        except Auth2FA:
            await bot.send_message(chat_id=user.user_id, text="Необходимо переаторизоваться в БАРС")
            async with async_session() as session:
                user_service = UserService(session)
                await user_service.set_bars_status_used(user.user_id, False)
            continue

        if not res:
            await bot.send_message(chat_id=user.user_id, text="Необходимо переаторизоваться в БАРС")
            async with async_session() as session:
                user_service = UserService(session)
                await user_service.set_bars_status_used(user.user_id, False)

        # Создаём watcher через фабрику
        bars_watcher = WatcherFactory.create_bars_watcher(user.user_id, auth, cache)
        res = await bars_watcher.start()
        await asyncio.sleep(1)
        if i % 15 == 0:
            time_to_sleep = 10
            logger.debug(f"Пауза {time_to_sleep} секунды, перед запуском остальных вотчеров")
            await asyncio.sleep(10)


async def recover_notifications_over_restarting_bot_osep():
    async with async_session() as session:
        user_service = UserService(session)
        users = await user_service.find_all_users_used_osep()

    cache = AsyncFileCacher(filename=WORKDIR / "cache.json")
    logger.info(f"Всего на перезапуск ОСЭП - {len(users)}")
    for i, user in enumerate(users, start=1):
        logger.info(f"Перезапускаю OsepWatcher после рестарта бота для {user.user_id} {user.osep_login} ({i}/{len(users)})")

        # Создаём auth и логинимся
        auth, session_obj = WatcherFactory.create_auth_and_session(
            user_id=user.user_id,
            service="osep",
            login=user.osep_login,
            password=user.osep_password,
            watcher_type=WatcherType.OSEP
        )

        res = await auth.login()
        if not res:
            continue

        # Создаём watcher через фабрику
        osep_watcher = WatcherFactory.create_osep_watcher(user.user_id, auth, cache)
        res = await osep_watcher.start()
        await asyncio.sleep(1)
        if i % 10 == 0:
            time_to_sleep = 10
            logger.debug(f"Пауза {time_to_sleep} секунды, перед запуском остальных вотчеров")
            await asyncio.sleep(time_to_sleep)
