"""Точка входа Telegram-бота: long polling + уведомления из событий.

Режимы задаются INTEGRATION_MODE:
  real — настоящие БАРС и ОСЭП;
  file — тестовый режим: ответы из simdata/*.json (можно править руками);
  sim  — «настоящие» запросы по сохранённым ответам (simdata/bars/*.html).
"""

from __future__ import annotations

import asyncio
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from loguru import logger

from src.bot.app import build_dispatcher
from src.bot.logsender import daily_log_send_loop
from src.bot.notifier import register_telegram_notifier
from src.bot.queue import create_bot_worker
from src.core.config import settings
from src.core.logger import setup_logging
from src.services.admins import (
    sync_admins_from_settings,
    sync_global_settings_from_env,
)
from src.services.attachments import AttachmentCache, cleanup_loop

HEALTHY_RUN_SECONDS = 60.0


async def _supervised(name: str, factory) -> None:
    """Фоновая задача под присмотром: упала (например, Redis перезапускался) —
    перезапускаем с паузой, а не оставляем мёртвой до перезапуска бота.

    Именно смерть потребителя очереди молча останавливала ВСЕ уведомления:
    проверки шли, события падали в очередь, никто их не читал.
    """
    backoff = 5.0
    while True:
        started = time.monotonic()
        try:
            await factory()
            return  # штатное завершение — для наших задач не ожидается
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "фоновая задача '{}' упала; перезапуск через {:.0f}с", name, backoff
            )
            if time.monotonic() - started > HEALTHY_RUN_SECONDS:
                backoff = 5.0  # долго жила — ошибка разовая, пробуем сразу
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)


async def main() -> None:
    setup_logging(settings.debug)
    # первый запуск: импорт админов и глобальных настроек из .env
    await sync_admins_from_settings()
    await sync_global_settings_from_env()
    bot = Bot(
        token=settings.telegram_bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp: Dispatcher = build_dispatcher()
    register_telegram_notifier(bot)
    # меню команд в Telegram (bot.set_my_commands); сначала чистим ранее
    # заданные списки команд (в т.ч. /admin) — он работает при ручном вводе
    await bot.delete_my_commands()
    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Главное меню"),
            BotCommand(command="report", description="Жалоба администратору"),
            BotCommand(command="suggestion", description="Предложение администратору"),
        ]
    )
    cleanup_task = asyncio.create_task(
        _supervised("cleanup вложений", lambda: cleanup_loop(AttachmentCache()))
    )
    # логи админам каждый день в 00:00 МСК (как в main-ветке)
    logsend_task = asyncio.create_task(
        _supervised("отправка логов", lambda: daily_log_send_loop(bot))
    )
    # воркер очереди `bot`: события (marks.changed и пр.) → уведомления;
    # работает в этом же event loop, поэтому не run_worker (свой loop),
    # а Worker.async_run() фоновой задачей
    queue_worker = create_bot_worker()
    # потребитель очереди уведомлений — под присмотром: его смерть = тишина
    # ВСЕХ уведомлений при живых проверках (так и было после падения Redis)
    queue_task = asyncio.create_task(
        _supervised("очередь bot", queue_worker.async_run)
    )
    logger.info(
        "бот запущен (long polling + очередь bot), integration_mode={}",
        settings.integration_mode,
    )
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception:
        # любая необработанная ошибка — в лог-файл с traceback, дальше наверх
        logger.exception("бот остановлен с ошибкой")
        raise
    finally:
        cleanup_task.cancel()
        logsend_task.cancel()
        queue_task.cancel()
        try:
            await queue_worker.close()
        except AttributeError:
            # arq на Windows падает в close() на SIGUSR1 — не критично,
            # процесс завершается, пул соединений закроется сам
            logger.debug("закрытие воркера очереди: пропущено (Windows/SIGUSR1)")


if __name__ == "__main__":
    asyncio.run(main())
