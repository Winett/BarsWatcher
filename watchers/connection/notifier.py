from typing import Awaitable, Callable, TYPE_CHECKING, Any, Generic, TypeVar
from watchers.notifier.base import BaseNotifier

if TYPE_CHECKING:
    from .base import ConnectionStatus


class ConnectionNotifier(BaseNotifier):

    async def notify(self, new_event: "ConnectionStatus") -> None:
        await super().notify(new_event)


