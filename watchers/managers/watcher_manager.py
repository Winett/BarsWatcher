from typing import Dict, Optional, TypeVar, ClassVar, TYPE_CHECKING, Generic
import asyncio
from abc import ABC, abstractmethod

from collections import defaultdict

from watchers.models.connection_monitor_models import ConnectionStatus
if TYPE_CHECKING:
    from watchers import BarsWatcher, OsepWatcher
from watchers.core.base_watcher import BaseWatcher
from watchers.models.watcher_models import UserCredentials, WatcherType, WatcherEvent, EventType
from loguru import logger

from watchers.utils.exceptions import AuthError
from watchers.services.notification_service import TelegramNotificationService
from watchers.connectors.connection_monitor import BarsMonitor, OsepMonitor, BaseConnectionMonitor

# T = TypeVar('T', bound='BaseWatcher')
# C = TypeVar('C', bound='BaseConnectionMonitor')

class WatcherManager(ABC):
    _managers_watchers: dict[str, dict[int, BaseWatcher]] = defaultdict(dict)
    notification_service = TelegramNotificationService()


    @classmethod
    def _get_watchers(cls) -> dict[int, BaseWatcher]:
        return cls._managers_watchers[cls.__name__]


    @classmethod
    async def process_connection_event(cls, conn_event: ConnectionStatus):
        if conn_event.CONNECTED:
            await cls.resume_all()
        elif conn_event.DISCONNECTED:
            await cls.pause_all()
        else:
            logger.warning(f"{cls.__name__} Неизвестный статус соединения: {conn_event}")

    @classmethod
    def register_watcher(cls, user_id: int, watcher: BaseWatcher):
        cls._get_watchers()[user_id] = watcher
        watcher.subscribe(cls._handle_watcher_event)

    @classmethod
    async def register_watcher_and_start(cls, user_id: int, watcher: BaseWatcher):
        cls.register_watcher(user_id, watcher)
        await watcher.start()

    @classmethod
    def unregister_watcher(cls, user_id: int):
        watcher = cls._get_watchers().pop(user_id, None)
        if watcher:
            watcher.unsubscribe(cls._handle_watcher_event)

    @classmethod
    def get_watcher_instance(cls, user_id: int) -> Optional[BaseWatcher]:
        return cls._get_watchers().get(user_id, None)

    @classmethod
    async def _handle_watcher_event(cls, event: WatcherEvent):
        """Обработка событий от вотчеров"""
        #TODO: Добавить общение с БД
        match event.event_type:
            case EventType.NEW_CHANGE:
                logger.debug(cls.__name__ + f" | {event.username} | {event.message if event.watcher_type == WatcherType.BARS else f'Новое письмо'}")
                # await self.notification_service.send_message(event.user_id, event.message)
                await cls.notification_service.send_message_with_documents(event.user_id, event.message, files=event.metadata.get('files', []))
            case EventType.EXCEPTION:
                match event.error:
                    case error if isinstance(error, AuthError):
                        await cls.notification_service.send_message(event.user_id, "Неверный логин или пароль")
                        cls.unregister_watcher(event.user_id)
                    case _:
                        await asyncio.sleep(5)
                        await cls.get_watcher_instance(event.user_id).restart()
                # await self.notification_service.send_message(event.user_id, event.message)
            case _:
                logger.warning(f"{cls.__name__} Неизвестное событие: {event.event_type}")

    @classmethod
    def _get_all_not_started_instance(cls) -> list[BaseWatcher]:
        return [user_watcher for user_watcher in cls._get_watchers().values() if not user_watcher.is_running]

    @classmethod
    async def pause_all(cls):
        logger.info(f"{cls.__name__} | Пауза всех вотчеров")
        cnt = 0
        for user_watcher in cls._get_watchers().values():
            await user_watcher.pause()
            cnt += 1
        logger.info(f"{cls.__name__} | Пауза {cnt} вотчеров")

    @classmethod
    async def resume_all(cls):
        logger.info(f"{cls.__name__} | Возобновление всех вотчеров")
        cnt = 0
        for user_watcher in cls._get_watchers().values():
            await user_watcher.resume()
            cnt += 1
        logger.info(f"{cls.__name__} | Возобновление {cnt} вотчеров")

    @classmethod
    async def start_all(cls):
        instances = cls._get_all_not_started_instance()
        logger.debug(f"{cls.__name__} Запуск {len(instances)} вотчеров")
        for user_watcher in instances:
            await user_watcher.start()

    @classmethod
    async def stop_all(cls):
        for user_watcher in cls._get_watchers().values():
            await user_watcher.stop()


class BarsWatcherManager(WatcherManager):
    pass


class OsepWatcherManager(WatcherManager):
    pass



# class WatcherManager:
#
#     _instance = None
#
#     def __new__(cls, *args, **kwargs):
#         if not cls._instance:
#             cls._instance = super().__new__(cls)
#         return cls._instance
#
#     def __init__(self):
#         if hasattr(self, 'initialized'):
#             return
#
#         self._watchers: Dict[int, Dict[WatcherType, BaseWatcher]] = defaultdict(dict)
#
#         self.notification_service = TelegramNotificationService()
#
#         self.initialized = True
#         self._logger_template = f"{self.__class__.__name__} | "
#
#     async def create_watcher(self, credentials: UserCredentials,
#                              watcher_class, services) -> Optional[BaseWatcher]:
#         """Создание нового вотчера"""
#         try:
#             watcher = watcher_class(credentials, **services)
#             await self.add_watcher(credentials.user_id, watcher)
#             return watcher
#         except Exception as e:
#             logger.error(f"{self._logger_template} Ошибка создания вотчера: {e}")
#             return None
#
#     async def add_watcher(self, user_id: int, watcher: BaseWatcher):
#         """Добавление вотчера в менеджер"""
#         self._watchers[user_id][watcher.credentials.watcher_type] = watcher
#         watcher.subscribe(self._handle_watcher_event)
#         logger.info(f"{self._logger_template} Добавлен вотчер для пользователя {user_id}")
#
#     async def remove_watcher(self, user_id: int, watcher_type: WatcherType):
#         """Удаление вотчера"""
#         if user_id in self._watchers and watcher_type in self._watchers[user_id]:
#             watcher = self._watchers[user_id].pop(watcher_type)
#             watcher.unsubscribe(self._handle_watcher_event)
#             await watcher.stop()
#
#             if not self._watchers[user_id]:
#                 del self._watchers[user_id]
#
#     async def _handle_watcher_event(self, event: WatcherEvent):
#         """Обработка событий от вотчеров"""
#         #TODO: Добавить общение с БД
#         match event.event_type:
#             case EventType.NEW_CHANGE:
#                 logger.debug(self._logger_template + f"{event.username} | {event.message if event.watcher_type == WatcherType.BARS else f'Новое письмо'}")
#                 # await self.notification_service.send_message(event.user_id, event.message)
#                 await self.notification_service.send_message_with_documents(event.user_id, event.message, files=event.metadata.get('files', []))
#             case EventType.EXCEPTION:
#                 match event.error:
#                     case error if isinstance(error, AuthError):
#                         await self.notification_service.send_message(event.user_id, "Неверный логин или пароль")
#                         await self.remove_watcher(event.user_id, event.watcher_type)
#                     case _:
#                         await asyncio.sleep(5)
#                         await self.get_watcher(event.user_id, event.watcher_type).restart()
#                 # await self.notification_service.send_message(event.user_id, event.message)
#             case _:
#                 logger.warning(f"{self._logger_template} Неизвестное событие: {event.event_type}")
#
#     def get_watcher(self, user_id: int, watcher_type: WatcherType) -> Optional[BaseWatcher]:
#         """Получение вотчера по пользователю и типу"""
#         if user_id in self._watchers and watcher_type in self._watchers[user_id]:
#             return self._watchers[user_id][watcher_type]
#         return None
#
#     async def start_all(self):
#         """Запуск всех вотчеров"""
#         tasks = []
#         for user_watchers in self._watchers.values():
#             for watcher in user_watchers.values():
#                 tasks.append(watcher.start())
#
#         await asyncio.gather(*tasks, return_exceptions=True)
#
#     async def stop_all(self):
#         """Остановка всех вотчеров"""
#         tasks = []
#         for user_watchers in self._watchers.values():
#             for watcher in user_watchers.values():
#                 tasks.append(watcher.stop())
#
#         await asyncio.gather(*tasks, return_exceptions=True)
#
#
# class BarsWatcherManager(WatcherManager):
#     class_type: type[BaseWatcher] = BarsWatcher
#     _watchers: dict[int, class_type] = {}
#
#     async def add_watcher(self, user_id: int, watcher: class_type):
#         await super().add_watcher(user_id, watcher)
#
#     async def add_watcher_and_start(self, user_id: int, watcher: class_type):
#         await self.add_watcher(user_id, watcher)
#         await watcher.start()
#
#     async def get_watcher(self, user_id: int) -> Optional[BaseWatcher]:
#         return super().get_watcher(user_id, WatcherType.BARS)