from loguru import logger

from database.db import async_session
from services.user import UserService

from watchers.notificator import BarsNotificator, OsepNotificator


async def recover_notifications_over_restarting_bot():
    async with async_session() as session:
        user_service = UserService(session)
        users = await user_service.find_all_users_used_bars()
    logger.disable("watchers.notificator")
    for user in users:
        logger.info(f"Перезапускаю нотификатор после рестарта бота для {user.user_id} {user.bars_login}")
        await BarsNotificator(user.user_id, user.bars_login, user.bars_password).start_watching()
    logger.enable("watchers.notificator")

async def recover_notifications_over_restarting_bot_osep():
    async with async_session() as session:
        user_service = UserService(session)
        users = await user_service.find_all_users_used_osep()
    logger.disable("watchers.notificator")
    for user in users:
        logger.info(f"Перезапускаю нотификатор после рестарта бота для {user.user_id} {user.osep_login}")
        await OsepNotificator(user.user_id, user.osep_login, user.osep_password).start_watching()
    logger.enable("watchers.notificator")