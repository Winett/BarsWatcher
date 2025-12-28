from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

from loguru import logger

T = TypeVar("T")

Callback = Callable[[T], None] | Callable[[T], Awaitable[None]]

class BaseNotifier:
    def __init__(self, logger_template: str = "") -> None:
        self._callbacks: set[Callback] = set()
        self._logger_template = logger_template

    @property
    def count(self) -> int:
        return len(self._callbacks)

    def subscribe(self, callback: Callback) -> bool:
        before = len(self._callbacks)
        self._callbacks.add(callback)
        return len(self._callbacks) != before

    def unsubscribe(self, callback: Callback) -> bool:
        before = len(self._callbacks)
        self._callbacks.discard(callback)
        return len(self._callbacks) != before

    async def notify(self, new_event: T) -> None:
        tasks = []
        for callback in self._callbacks.copy():
            try:
                if asyncio.iscoroutinefunction(callback):
                    tasks.append(asyncio.create_task(callback(new_event)))
                else:
                    callback(new_event)
            except Exception as e:
                logger.error(self._logger_template + f"Ошибка при обработке callback: {e}")

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)