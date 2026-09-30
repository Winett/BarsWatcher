"""Защита от повторной обработки обновлений (сон/пробуждение ПК).

Пока ПК спит, Telegram копит обновления и после пробуждения может отдать
их заново (offset не подтверждён) — бот начинает «переотвечать» старым
сообщениям и кнопкам. Дедупликация по update_id + отбрасывание устаревших
сообщений это закрывают.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update
from loguru import logger

from src.events import get_redis

__all__ = ["StaleUpdateMiddleware"]

log = logger.bind(component="bot")

SEEN_TTL = 24 * 3600  # сутки: держим обработанные update_id
STALE_AFTER = timedelta(minutes=10)  # старее — не выполняем


class StaleUpdateMiddleware(BaseMiddleware):
    """Пропускает повторные и устаревшие обновления."""

    async def __call__(self, handler, event: TelegramObject, data: dict) -> object:
        if not isinstance(event, Update):
            return await handler(event, data)

        redis = await get_redis()
        key = f"tg:seen:{event.update_id}"
        if not await redis.set(key, "1", nx=True, ex=SEEN_TTL):
            log.warning("update {}: дубликат, пропуск", event.update_id)
            return None

        # устаревшие сообщения (callback не проверяем: дата сообщения-панели
        # не годится — само нажатие может быть свежим, а панель старой)
        message = event.message or event.edited_message
        if message is not None and message.date is not None:
            sent = message.date
            if sent.tzinfo is None:
                sent = sent.replace(tzinfo=UTC)
            if datetime.now(UTC) - sent > STALE_AFTER:
                log.warning(
                    "update {}: сообщение устарело, пропуск ({})",
                    event.update_id,
                    sent,
                )
                return None

        return await handler(event, data)
