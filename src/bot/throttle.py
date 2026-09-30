"""Ограничение скорости отправки в Telegram.

Telegram принимает около 30 сообщений в секунду глобально — держим ≤20/с
с запасом. Если Telegram всё же вернул 429 (TelegramRetryAfter) — спим
столько, сколько он просит, и повторяем отправку.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable

from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from loguru import logger

__all__ = ["Throttle", "throttle", "send_with_retry"]

MAX_PER_SECOND = 20


class Throttle:
    """Скользящее окно: не более N отправок в секунду (на процесс)."""

    def __init__(self, max_per_second: int = MAX_PER_SECOND):
        self._limit = max_per_second
        self._stamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                while self._stamps and now - self._stamps[0] >= 1.0:
                    self._stamps.popleft()
                if len(self._stamps) < self._limit:
                    self._stamps.append(now)
                    return
                await asyncio.sleep(max(1.0 - (now - self._stamps[0]), 0.05))


throttle = Throttle()


async def send_with_retry(
    factory: Callable[[], Awaitable], *, attempts: int = 3
):
    """Отправка с учётом rate limit и повтором.

    Повторяем при: 429 (TelegramRetryAfter — ждём, сколько просит),
    500 (TelegramServerError) и сетевых сбоях (TelegramNetworkError) —
    с короткой паузой.
    """
    last_error: Exception | None = None
    for _ in range(attempts):
        await throttle.wait()
        try:
            return await factory()
        except TelegramRetryAfter as exc:
            last_error = exc
            logger.warning("Telegram просит подождать {}с (429)", exc.retry_after)
            await asyncio.sleep(float(exc.retry_after) + 0.5)
        except (TelegramServerError, TelegramNetworkError) as exc:
            last_error = exc
            logger.warning("Telegram 500/сеть, повтор через 2с: {}", exc)
            await asyncio.sleep(2.0)
    if last_error is not None:
        raise last_error
    raise RuntimeError("send_with_retry: не выполнено")  # недостижимо
