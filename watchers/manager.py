
import asyncio
from typing import Type, ClassVar

from collections import defaultdict

from loguru import logger

from .bars_watcher import BarsWatcher
from .base import BaseWatcher, WatcherEvent
from .connection.base import BaseConnectionMonitor, BarsConnectionMonitor, ConnectionStatus, OsepConnectionMonitor
from .fetcher.exceptions import AuthError
from .notificator.model import NotificatorMessage, WatcherType
from .osep_watcher import OsepWatcher


class WatcherManager:

    watcher_instance = Type[BaseWatcher]
    connection_class: Type[BaseConnectionMonitor]

    def __init__(self):
        self._watchers = defaultdict(dict)
        self._connection_class = self.connection_class()

        self._logger_template = f"{self.__class__.__name__} | "

        self._connection_class.subscribe(self.on_change_connection_status)

    def __del__(self):
        self._connection_class.unsubscribe(self.on_change_connection_status)

    def on_change_watcher_status(self, event: WatcherEvent) -> None:
        logger.info(f"{self._logger_template} {event}")
        if isinstance(event.error, AuthError):
            logger.error(f"В связи с ошибкой авторизации {event.error} для {event.watcher_name} у пользователя {event.username} остановлен")
            watcher = self._watchers[event.user_id][event.watcher_name]
            watcher_type = WatcherType.OSEP if isinstance(watcher, OsepWatcher) else WatcherType.BARS
            msg = NotificatorMessage(message=f"В связи с ошибкой авторизации прослушивание {watcher_type.value} остановлено", user_id=event.user_id, watcher=watcher_type)
            asyncio.create_task(watcher.notifier.notify(msg))
            self.remove(event.user_id, event.watcher_name)
            # TODO: отправить уведомление и проработать с БД
            return
        elif isinstance(event.error, Exception):
            logger.error(self._logger_template + f"Ошибка {event.error} для {event.watcher_name} у пользователя {event.username}, пробую перезапустить")
            watcher = self._watchers[event.user_id][event.watcher_name]
            asyncio.create_task(watcher.start_watching())
        





    def add(self, user_id: int, watcher: BaseWatcher) -> None:
        self._watchers[user_id].update({watcher.__class__.__name__: watcher})
        watcher.subscribe(self.on_change_watcher_status)

    def remove(self, user_id: int, watcher_name: str) -> None:
        watcher = self._watchers[user_id].pop(watcher_name)
        watcher.unsubscribe(self.on_change_watcher_status)
        if not self._watchers[user_id]:
            del self._watchers[user_id]

    def remove_all(self) -> None:
        for user_id, watchers in list(self._watchers.items()):
            for watcher in list(watchers.values()):
                watcher.unsubscribe(self.on_change_watcher_status)
            self._watchers[user_id].clear()
        self._watchers.clear()

    def get(self, user_id: int) -> dict[str, BaseWatcher]:
        return self._watchers.get(user_id)

    def get_all(self) -> dict[int, dict[str, BaseWatcher]]:
        return self._watchers

    async def stop_all(self) -> None:
        watchers_to_stop = []
        for user_id in self._watchers:
            for watcher in self._watchers[user_id].values():
                watchers_to_stop.append(asyncio.create_task(watcher.stop_watching()))
        await asyncio.gather(*watchers_to_stop)
        self.remove_all()

    async def start_all(self) -> None:
        watchers_to_start = []
        for user_id in self._watchers:
            for watcher in self._watchers[user_id].values():
                watchers_to_start.append(asyncio.create_task(watcher.start_watching()))
        await asyncio.gather(*watchers_to_start)

    async def start(self, user_id: int, watcher_name: str) -> bool:
        user_watchers = self._watchers.get(user_id)
        if not user_watchers:
            return False
        watcher = user_watchers.get(watcher_name)
        if not watcher:
            return False
        await watcher.start_watching()
        return True

    async def stop(self, user_id: int, watcher_name: str) -> bool:
        user_watchers = self._watchers.get(user_id)
        if not user_watchers:
            return False
        watcher = user_watchers.get(watcher_name)
        if not watcher:
            return False
        await watcher.stop_watching()
        return True

    async def restart(self, user_id: int, watcher_name: str) -> bool:
        a = await self.stop(user_id, watcher_name)
        b = await self.start(user_id, watcher_name)
        return a and b

    async def restart_all(self) -> None:
        await self.stop_all()
        await self.start_all()

    def pause(self, user_id: int, watcher_name: str) -> bool:
        user_watchers = self._watchers.get(user_id)
        if not user_watchers:
            return False
        watcher = user_watchers.get(watcher_name)
        if not watcher:
            return False
        watcher.pause()
        return True

    def pause_all(self) -> None:
        for user_id in self._watchers:
            for watcher in self._watchers[user_id].values():
                watcher.pause()

    def resume(self, user_id: int, watcher_name: str) -> bool:
        user_watchers = self._watchers.get(user_id)
        if not user_watchers:
            return False
        watcher = user_watchers.get(watcher_name)
        if not watcher:
            return False
        watcher.resume()
        return True

    def resume_all(self) -> None:
        for user_id in self._watchers:
            for watcher in self._watchers[user_id].values():
                watcher.resume()

    async def on_change_connection_status(self, new_status: ConnectionStatus) -> None:
        if new_status == ConnectionStatus.CONNECTED:
            self.resume_all()
        elif new_status == ConnectionStatus.DISCONNECTED:
            self.pause_all()


class BarsWatcherManager(WatcherManager):
    watcher_instance = BarsWatcher
    connection_class = BarsConnectionMonitor

    def __init__(self):
        super().__init__()

class OsepWatcherManager(WatcherManager):
    watcher_instance = OsepWatcher
    connection_class = OsepConnectionMonitor

    def __init__(self):
        super().__init__()


class WatcherManagerFactory:

    _managers: dict[Type[BaseWatcher], WatcherManager] = {}

    @classmethod
    def get_manager(cls, watcher_class: Type[BaseWatcher]) -> WatcherManager:
        if watcher_class not in cls._managers:
            manager = None
            for watcher in WatcherManager.__subclasses__():
                if watcher.watcher_instance == watcher_class:
                    manager = watcher()
                    break
            cls._managers[watcher_class] = manager

        return cls._managers[watcher_class]




