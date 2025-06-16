import asyncio
from sys import stderr
from datetime import timedelta, timezone

from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InputMediaDocument, FSInputFile
from aiogram.types.bot_command import BotCommand
from aiogram.fsm.storage.memory import MemoryStorage

from settings import settings
from loguru import logger

from handlers import router as handlers_router

from database.db import init_db
from middlewares.db import DatabaseMiddleware

from services.notification import NotificationService
from watchers.notificator import Notificator
from watchers.base import BaseAuth

from loader import recover_notifications_over_restarting_bot, recover_notifications_over_restarting_bot_osep

from _io import TextIOWrapper
from pathlib import Path


logger.remove()

utc_plus_3 = timezone(timedelta(hours=0))
def format_time_utc3(record):
    record.update(time=record['time'].astimezone(utc_plus_3))
logger = logger.patch(format_time_utc3)

logger.add(stderr, format="<white>{time:HH:mm:ss:Z}</white>"
                          " | <level>{level: <8}</level>"
                          " | {name}:{function}:{line}"
                          " | <cyan>{line}</cyan>"
                          " - <magenta>{message}</magenta>", level="DEBUG")

bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode="HTML")
    )

def check_log_size_and_send_to_admin(message: str, log: TextIOWrapper) -> bool:
    if Path(log.name).stat().st_size > 5 * 1024 * 1024:
        for admin in settings.admins:
            asyncio.create_task(bot.send_document(chat_id=admin, document=FSInputFile(path=Path(log.name)), caption="Лог бота"))
        return True
    return False

logger.add('log.log', rotation=check_log_size_and_send_to_admin) #каждые 5 МБ

(Path(__file__).parent / "sessions").mkdir(exist_ok=True, parents=True)
BaseAuth.session_dir = Path(__file__).parent / "sessions"



async def main():
    print(f'{settings.DEBUG = }')

    await init_db()
    logger.info('База данных инициализирована!')
    Notificator.bot = bot
    NotificationService.bot = bot

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



