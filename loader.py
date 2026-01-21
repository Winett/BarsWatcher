from loguru import logger
import asyncio

from database.db import async_session
from services.user import UserService
from settings import WORKDIR
from watchers.models.watcher_models import UserCredentials, WatcherType
from watchers.services.cache_service import AsyncFileCacher

from watchers import OsepWatcher, BarsWatcher

async def recover_notifications_over_restarting_bot():
    async with async_session() as session:
        user_service = UserService(session)
        users = await user_service.find_all_users_used_bars()
    # logger.disable("watchers")
    cache = AsyncFileCacher(filename=WORKDIR / "cache.json")
    logger.info(f"Всего на перезапуск БАРС - {len(users)}")
    for i, user in enumerate(users, start=1):
        logger.info(f"Перезапускаю BarsWatcher после рестарта бота для {user.user_id} {user.bars_login} ({i}/{len(users)})")
        bars_watcher = BarsWatcher(
            credentials=UserCredentials(user_id=user.user_id, username=user.bars_login, password=user.bars_password, watcher_type=WatcherType.BARS),
            cache_service=cache,
        )
        res = await bars_watcher.start()
        await asyncio.sleep(1)
        if i % 15 == 0:
            time_to_sleep = 10
            logger.debug(f"Пауза {time_to_sleep} секунды, перед запуском остальных вотчеров")
            await asyncio.sleep(10)
        # if not res:
        #     await user_service.set_bars_status_used(user.user_id, False)
    # logger.enable("watchers")

async def recover_notifications_over_restarting_bot_osep():
    async with async_session() as session:
        user_service = UserService(session)
        users = await user_service.find_all_users_used_osep()
    # logger.disable("watchers")
    cache = AsyncFileCacher(filename=WORKDIR / "cache.json")
    logger.info(f"Всего на перезапуск ОСЭП - {len(users)}")
    for i, user in enumerate(users, start=1):
        logger.info(f"Перезапускаю OsepWatcher после рестарта бота для {user.user_id} {user.osep_login} ({i}/{len(users)})")
        # manager = WatcherManagerFactory.get_manager(OsepWatcher)
        osep_watcher = OsepWatcher(
            credentials=UserCredentials(user_id=user.user_id, username=user.osep_login, password=user.osep_password, watcher_type=WatcherType.OSEP),
            cache_service=cache,
        )
        res = await osep_watcher.start()
        await asyncio.sleep(1)
        if i % 10 == 0:
            time_to_sleep = 10
            logger.debug(f"Пауза {time_to_sleep} секунды, перед запуском остальных вотчеров")
            await asyncio.sleep(time_to_sleep)
        # if not res:
        #     await user_service.set_osep_status_used(user.user_id, False)
    # logger.enable("watchers")