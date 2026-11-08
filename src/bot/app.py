"""Сборка Telegram-бота (long polling)."""

from __future__ import annotations

from aiogram import Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from loguru import logger

from .admin import router as admin_router
from .handlers import build_router
from .input_log import InputLoggingMiddleware
from .stale_guard import StaleUpdateMiddleware

__all__ = ["build_dispatcher"]


async def _on_error(event) -> bool:
    """Ошибка хендлера — в лог-файл с traceback (не только в stderr aiogram)."""
    logger.opt(exception=event.exception).error(
        "ошибка обработки апдейта | update_id={}",
        getattr(event.update, "update_id", "?"),
    )
    return True  # обработано: пользователю ничего не шлём (как и раньше)


def build_dispatcher() -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    dp.errors.register(_on_error)
    # после сна ПК Telegram отдаёт обновления повторно — отсекаем дубликаты
    # и устаревшие сообщения
    dp.update.outer_middleware(StaleUpdateMiddleware())
    # логирование ввода (Message/CallbackQuery) — на все хендлеры
    dp.message.middleware(InputLoggingMiddleware())
    dp.callback_query.middleware(InputLoggingMiddleware())
    # команды (build_router) регистрируются раньше админки: /start и другие
    # команды должны срабатывать, даже если включён FSM-ввод (рассылка и т.п.)
    dp.include_router(build_router())
    dp.include_router(admin_router)
    return dp
