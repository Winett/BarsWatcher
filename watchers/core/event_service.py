from typing import Any, Callable
import asyncio
from loguru import logger


class EventService:
    """Синглтон pub/sub для событий вотчеров."""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._subscribers: set[Callable] = set()
        self._initialized = True

    def subscribe(self, callback: Callable[[Any], Any]):
        self._subscribers.add(callback)

    def unsubscribe(self, callback: Callable[[Any], Any]):
        self._subscribers.discard(callback)

    def notify_subscribers(self, event: Any):
        for subscriber in list(self._subscribers):
            if asyncio.iscoroutinefunction(subscriber):
                task = asyncio.create_task(subscriber(event))
                task.add_done_callback(self._event_callback_for_coroutine)
            else:
                subscriber(event)

    def _event_callback_for_coroutine(self, task: asyncio.Task):
        if task.exception():
            logger.error("Ошибка в подписчике EventService", exc_info=task.exception())

    def close(self):
        self._subscribers.clear()
