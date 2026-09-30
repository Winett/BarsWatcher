"""Логирование пользовательского ввода: Message / CallbackQuery.

Пишется всё, что вводит пользователь (текст, нажатые кнопки) и тип апдейта —
чтобы потом можно было восстановить действия перед ошибкой. Пароль в лог не
попадает: вместо значения маркер.

Здесь же синхронизация пользователя при любом контакте: актуальный username,
возврат is_active (пользователь снова пишет — бот разблокирован). Заблокированные
админом (is_blocked) полностью игнорируются.
"""

from __future__ import annotations

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject
from aiogram.types import User as TgUser
from loguru import logger

from src.db.database import session_factory
from src.repositories.uow import UnitOfWork

from .handlers import LoginFlow

log = logger.bind(component="input")

__all__ = ["InputLoggingMiddleware"]

_MAX_LEN = 300


async def _sync_user(tg_user: TgUser) -> bool:
    """Обновить username в БД. True — пользователь заблокирован (игнор).

    is_active здесь НЕ трогаем: иначе нажатие админом «Вернуть активность»
    сначала автоматически делало пользователя активным, а тоггл в хендлере
    переворачивал значение обратно. Активность меняется только из админки
    (и автоматически — при блокировке бота пользователем).
    """
    async with session_factory() as session:
        uow = UnitOfWork(session)
        user = await uow.users.get_by_telegram_id(tg_user.id)
        if user is None:
            return False  # неизвестный — создаст cmd_start
        if user.is_blocked:
            return True
        if user.username != tg_user.username:
            log.info(
                "username изменён: {} → {} | user={}",
                user.username or "-",
                tg_user.username or "-",
                tg_user.id,
            )
            user.username = tg_user.username
            await uow.commit()
    return False


class InputLoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event: TelegramObject, data: dict):
        tg_user = getattr(event, "from_user", None)
        if tg_user is not None and await _sync_user(tg_user):
            return None  # заблокирован админом — полностью игнорируем
        if isinstance(event, Message):
            user = event.from_user
            state: FSMContext | None = data.get("state")
            state_name = await state.get_state() if state else None
            if state_name == LoginFlow.waiting_password.state:
                text = "<пароль, скрыт>"
            elif event.text:
                text = event.text
            elif event.caption:
                text = f"<{event.content_type}> подпись: {event.caption}"
            else:
                text = f"<{event.content_type}>"
            log.info(
                "ввод | Message | user={} (@{}) | {}",
                user.id,
                user.username or "-",
                text[:_MAX_LEN],
            )
        elif isinstance(event, CallbackQuery):
            user = event.from_user
            log.info(
                "ввод | CallbackQuery | user={} (@{}) | data={}",
                user.id,
                user.username or "-",
                event.data,
            )
        return await handler(event, data)
