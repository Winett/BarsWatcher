import asyncio
from sys import stderr
from pathlib import Path

from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.types.bot_command import BotCommand
from aiogram.fsm.storage.memory import MemoryStorage

from settings import settings
from loguru import logger

from handlers import router as handlers_router

from database.db import init_db
from middlewares.db import DatabaseMiddleware

from watchers.notificator import Notificator
from watchers.base import BaseAuth

from loader import recover_notifications_over_restarting_bot, recover_notifications_over_restarting_bot_osep



logger.remove()
if not settings.DEBUG:
    logger.add(stderr, format="<white>{time:HH:mm:ss:Z}</white>"
                              " | <level>{level: <8}</level>"
                              " | <cyan>{line}</cyan>"
                              " - <magenta>{message}</magenta>")
# logger.add(stderr)
logger.add('log.log', rotation=8*1024*1024*5) #каждые 5 МБ
(Path(__file__).parent / "sessions").mkdir(exist_ok=True, parents=True)
BaseAuth.session_dir = Path(__file__).parent / "sessions"



async def main():
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode="HTML")
    )
    await init_db()
    logger.info('База данных инициализирована!')
    Notificator.bot = bot

    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(DatabaseMiddleware())
    dp.callback_query.middleware(DatabaseMiddleware())


    dp.include_routers(
        handlers_router
    )

    await recover_notifications_over_restarting_bot()
    await recover_notifications_over_restarting_bot_osep()
    about = await bot.get_me()

    await bot.set_my_commands([
        BotCommand(command='/start', description='Перезапустить бота')
    ])
    await bot.delete_webhook(drop_pending_updates=True)
    logger.info(f'Бот запущен! bot_id = {about.id} username = {about.username}')

    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())



