from loguru import logger

from database.db import async_session
from services.user import UserService

from watchers.notificator import BarsNotificator, OsepNotificator


async def recover_notifications_over_restarting_bot():
    async with async_session() as session:
        user_service = UserService(session)
        users = await user_service.find_all_users_used_bars()
    logger.disable("watchers")
    for user in users:
        logger.info(f"Перезапускаю нотификатор после рестарта бота для {user.user_id} {user.bars_login}")
        res = await BarsNotificator(user.user_id, user.bars_login, user.bars_password).start_watching()
        if not res:
            await user_service.set_bars_status_used(user.user_id, False)
    logger.enable("watchers")

async def recover_notifications_over_restarting_bot_osep():
    async with async_session() as session:
        user_service = UserService(session)
        users = await user_service.find_all_users_used_osep()
    logger.disable("watchers")
    for user in users:
        logger.info(f"Перезапускаю нотификатор после рестарта бота для {user.user_id} {user.osep_login}")
        res = await OsepNotificator(user.user_id, user.osep_login, user.osep_password).start_watching()
        if not res:
            await user_service.set_osep_status_used(user.user_id, False)
    logger.enable("watchers")