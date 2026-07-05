import aiogram
from aiohttp import web

from utils import setup_logger

import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types.bot_command import BotCommand
from aiogram.fsm.storage.memory import MemoryStorage

from watchers.connectors.connection_monitor import BarsMonitor, OsepMonitor
from watchers.managers.watcher_manager import OsepWatcherManager, BarsWatcherManager
from watchers.services.redis_cache_service import RedisCacheService
from watchers.services.watcher_factory import WatcherFactory
from watchers.session.pool_session import PoolSession
from webhook import create_prepared_webapp, WEBHOOK_HOST, WEBHOOK_PORT

from settings import settings, WORKDIR
from loguru import logger

from handlers import router as handlers_router

from database.db import init_db, async_session
from database.services.config_service import ConfigService
from middlewares.db import DatabaseMiddleware
from middlewares.throttling import ThrottlingMiddleware
from middlewares.logging import LoggingMiddleware

from services.notification import NotificationService
from watchers.services.notification_service import TelegramNotificationService
from watchers.utils.file_cache_manager import FileCacheManager

from loader import recover_notifications_over_restarting_bot, recover_notifications_over_restarting_bot_osep
from webhook import WEBHOOK_BASE_URL, WEBHOOK_PATH
from pathlib import Path


def create_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(DatabaseMiddleware())
    dp.callback_query.middleware(DatabaseMiddleware())
    dp.update.outer_middleware(ThrottlingMiddleware())
    dp.update.outer_middleware(LoggingMiddleware())

    dp.include_routers(
        handlers_router
    )

    return dp


(WORKDIR / "sessions").mkdir(exist_ok=True, parents=True)


async def on_bot_startup(bot: Bot):
    logger.info('Запуск бота...')
    await init_db()
    logger.info('База данных инициализирована!')

    # Инициализация ConfigService
    config_service = ConfigService(async_session)
    WatcherFactory.set_config_service(config_service)
    BarsWatcherManager.set_config_service(config_service)
    OsepWatcherManager.set_config_service(config_service)
    logger.info('ConfigService инициализирован!')

    # Инициализация Redis
    cache = RedisCacheService(settings.redis_url)
    await cache.connect()
    WatcherFactory.set_cache_service(cache)
    logger.info('RedisCacheService инициализирован!')

    me = await bot.get_me()
    logger.info(f'Бот запущен! bot_id = {me.id} username = {me.username}')
    #===============

    await BarsMonitor().start_monitoring()
    await OsepMonitor().start_monitoring()
    await asyncio.sleep(1)
    BarsMonitor().subscribe(BarsWatcherManager.process_connection_event)
    OsepMonitor().subscribe(OsepWatcherManager.process_connection_event)

    asyncio.create_task(recover_notifications_over_restarting_bot(bot))
    asyncio.create_task(recover_notifications_over_restarting_bot_osep())

    await bot.set_my_commands([
        BotCommand(command='/start', description='Перезапустить бота'),
        BotCommand(command='/suggestion', description='Отправить предложение по улучшению бота'),
        BotCommand(command='/report', description='Отправить админу сообщение о неполадках'),
        BotCommand(command="/settings", description="Настройки вотчеров")
    ])

async def on_bot_shutdown(*args, **kwargs):
    # Отменить запланированные удаления файлов
    await FileCacheManager.cancel_all()

    cache = WatcherFactory.get_cache_service()
    if cache:
        await cache.close()

    BarsMonitor().unsubscribe(BarsWatcherManager.process_connection_event)
    OsepMonitor().unsubscribe(OsepWatcherManager.process_connection_event)
    await BarsMonitor().close()
    await OsepMonitor().close()

    # Закрываем все сессии в пуле
    await PoolSession.release_all()

async def setup_webhook(bot: Bot) -> bool:
    url = f"{WEBHOOK_BASE_URL}{WEBHOOK_PATH}"

    webhook_info = await bot.get_webhook_info()
    if webhook_info.url != url:
        await bot.delete_webhook(drop_pending_updates=True)
        try:
            await bot.set_webhook(url=url, )
        except aiogram.exceptions.TelegramBadRequest as error:
            logger.error(f'Ошибка при установке webhook! {error = }')
            return False
        logger.info(f"Новый webhook установлен! url = {url}")
        return True
    return True

async def main():
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode="HTML")
    )

    setup_logger(bot, settings.admins)

    print(f'{settings.DEBUG = }')
    TelegramNotificationService.set_bot_instance(bot)
    NotificationService.bot = bot

    dp = create_dispatcher()
    dp.startup.register(on_bot_startup)
    dp.shutdown.register(on_bot_shutdown)

    loop = asyncio.get_event_loop().set_debug(True)
    try:
        set_webhook = await setup_webhook(bot)
        if set_webhook:
            try:
                app = create_prepared_webapp(bot, dp)
                runner = web.AppRunner(app)
                await runner.setup()

                site = web.TCPSite(runner, WEBHOOK_HOST, WEBHOOK_PORT)
                await site.start()
                logger.info(f"Бот запущен на Webhooks")
                try:
                    await asyncio.Future()
                except KeyboardInterrupt:
                    logger.info('Остановка сервера...')
                finally:
                    await runner.cleanup()
                    await bot.session.close()

            except RuntimeError as error:
                logger.exception(error)
                logger.info(f"RuntimeError удаляю webhook")
                await bot.delete_webhook(drop_pending_updates=True)
                await dp.start_polling(bot)
        else:
            logger.info(f"Бот запущен на Long Polling")
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
    except KeyboardInterrupt:
        logger.warning(f"KeyboardInterrupt")


if __name__ == "__main__":
    asyncio.run(main())
