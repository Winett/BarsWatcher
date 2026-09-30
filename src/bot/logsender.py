"""Ежедневная отправка логов администраторам в 00:00 (МСК).

Аналог LogService.archive_and_send из main-ветки: раз в сутки вчерашний
лог упаковывается и уходит всем администраторам.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import FSInputFile
from loguru import logger

from src.core.timefmt import MSK
from src.events import get_redis
from src.services.logfiles import build_log_zips, format_size

__all__ = ["daily_log_send_loop"]

log = logger.bind(component="logs")


async def _send_day(bot: Bot, day) -> int:
    """Отправить логи за дату всем администраторам; вернуть число отправок."""
    from src.services.admins import admin_telegram_ids

    zips = build_log_zips(day)
    if not zips:
        log.info("рассылка логов: за {} файлов нет, пропуск", day.strftime("%d.%m.%Y"))
        return 0

    label = day.strftime("%d.%m.%Y")
    sent = 0
    for admin_id in await admin_telegram_ids():
        try:
            for i, zip_path in enumerate(zips):
                caption = f"📜 Логи за {label}"
                if len(zips) > 1:
                    caption += f" (часть {i + 1}/{len(zips)})"
                caption += f"\nРазмер: {format_size(zip_path.stat().st_size)}"
                await bot.send_document(
                    admin_id, FSInputFile(zip_path), caption=caption
                )
            sent += 1
        except Exception as error:
            log.warning("рассылка логов: не отправлено tg={}: {}", admin_id, error)
    log.info("рассылка логов: {} отправлено {} админам", label, sent)
    return sent


async def daily_log_send_loop(bot: Bot) -> None:
    """В 00:00 МСК отправляет вчерашний лог администраторам (один раз в сутки)."""
    log.info("рассылка логов админам: раз в день в 00:00 МСК")
    while True:
        try:
            now = datetime.now(MSK)
            if now.hour == 0:
                redis = await get_redis()
                day_key = now.strftime("%Y-%m-%d")
                if await redis.set(
                    f"logs:daily:{day_key}", "1", nx=True, ex=48 * 3600
                ):
                    await _send_day(bot, now.date() - timedelta(days=1))
        except Exception as error:
            log.error("рассылка логов: {}", error)
        await asyncio.sleep(60)
