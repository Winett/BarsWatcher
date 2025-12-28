from watchers.notifier.base import BaseNotifier
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from watchers.base import WatcherEvent

class WatcherNotifier(BaseNotifier):
    async def notify(self, new_event: "WatcherEvent") -> None:
        await super().notify(new_event)