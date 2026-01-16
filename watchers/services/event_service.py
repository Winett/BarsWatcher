from typing import Any, Callable
import asyncio


class EventService:

    def __init__(self):
        self._subscribers = set()

    def subscribe(self, callback: Callable[[Any], Any]):
        self._subscribers.add(callback)

    def unsubscribe(self, callback: Callable[[Any], Any]):
        self._subscribers.discard(callback)

    def notify_subscribers(self, event: Any):
        for subscriber in self._subscribers:
            if asyncio.iscoroutinefunction(subscriber):
                asyncio.create_task(subscriber(event)).add_done_callback(self._event_callback_for_coroutine)
            else:
                subscriber(event)

    def _event_callback_for_coroutine(self, task: asyncio.Task):
        pass

    def close(self):
        self._subscribers.clear()


