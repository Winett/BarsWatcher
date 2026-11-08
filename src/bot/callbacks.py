"""Ответы на callback-запросы: кнопка не должна висеть с «часиками».

Telegram показывает спиннер на кнопке, пока callback не подтверждён ответом.
Ответить можно только один раз — повтор молча игнорируется (первый ответ
побеждает). Поэтому всплывающие подсказки вызываются ДО тихого подтверждения.
"""

from __future__ import annotations

import functools

from aiogram.types import CallbackQuery

__all__ = ["ack", "toast", "answers"]


async def ack(callback: CallbackQuery) -> None:
    """Тихо подтвердить callback (снять «часики»). Безопасен при повторе."""
    try:
        await callback.answer()
    except Exception:  # noqa: BLE001 — уже отвечено/устарело, не важно
        pass


async def toast(
    callback: CallbackQuery, text: str = "", *, show_alert: bool = False
) -> None:
    """Подтвердить callback подсказкой; первый ответ побеждает, повтор молчит."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except Exception:  # noqa: BLE001 — уже отвечено/устарело, не важно
        pass


def answers(fn):
    """Декоратор callback-хендлера: ГАРАНТИРУЕТ ответ на callback.

    Даже если хендлер упал или забыл ответить — кнопка не останется с
    «часиками». Всплывающие подсказки внутри хендлера вызываются раньше и
    остаются видимыми (второй ответ — тихий).
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        callback = args[0] if args else kwargs.get("callback")
        try:
            return await fn(*args, **kwargs)
        finally:
            if isinstance(callback, CallbackQuery):
                await ack(callback)

    return wrapper
