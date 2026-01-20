from typing import Dict, Optional, TypeVar, ClassVar, TYPE_CHECKING, Generic
import asyncio
from abc import ABC, abstractmethod

from collections import defaultdict

from watchers.models.connection_monitor_models import ConnectionStatus
from watchers.models.mail_models import AttachmentData

if TYPE_CHECKING:
    from watchers import BarsWatcher, OsepWatcher
from watchers.core.base_watcher import BaseWatcher
from watchers.models.watcher_models import UserCredentials, WatcherType, WatcherEvent, EventType, WatcherStatus
from loguru import logger
#================
from services.user import UserService
from database.db import async_session
from settings import settings
#================

from watchers.utils.exceptions import AuthError, DataParsingError, ResponseError, RequestVerificationTokenError
from uuid import uuid4
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
        match event.event_type:
            case EventType.NEW_CHANGE:
                logger.debug(cls.__name__ + f" | {event.username} | {event.message if event.watcher_type == WatcherType.BARS else f'Новое письмо'}")
                # await self.notification_service.send_message(event.user_id, event.message)
                await cls.notification_service.send_message_with_documents(event.user_id, event.message, files=event.metadata.get('files', []))
            case EventType.EXCEPTION:
                match event.error:
                    case error if isinstance(error, AuthError):
                        await cls.notification_service.send_message(event.user_id, f" [{event.watcher_type.value}] Неверный логин или пароль")
                        await cls.stop_and_delete(event.user_id)
                        #========================== Временное решение ============================
                        async with async_session() as session:
                            if event.watcher_type == WatcherType.BARS:
                                await UserService(session).set_bars_status_used(event.user_id, False)
                            elif event.watcher_type == WatcherType.OSEP:
                                await UserService(session).set_osep_status_used(event.user_id, False)
                        #==========================================================================
                    case error:
                        if isinstance(error, DataParsingError) or isinstance(error, ResponseError) or isinstance(error, RequestVerificationTokenError):
                            uid = uuid4().hex
                            content = error.content.encode(errors="ignore", encoding="utf-8") if isinstance(error.content, str) else error.content
                            att_data = AttachmentData(
                                id=uid,
                                content_type="application/html",
                                filename=f"{event.username}_{event.user_id}_{event.watcher_type.value}.html",
                                size=len(content),
                                content=content,
                            )
                            for admin in settings.admins:
                                await cls.notification_service.send_message_with_documents(admin, f"Ошибка при обработке запроса: {type(error).__name__} у {event.username} <code>{event.user_id}</code>", files=[att_data])
                        logger.exception(error)
                        await asyncio.sleep(5)
                        try:
                            await cls.get_watcher_instance(event.user_id).restart()
                        except AttributeError:
                            pass
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

    @classmethod
    async def stop_and_delete(cls, user_id: int):
        watcher = cls.get_watcher_instance(user_id)
        if watcher:
            await watcher.stop()
            await watcher.close()
            cls.unregister_watcher(user_id)

    @classmethod
    def watcher_stats(cls):
        stats = {}
        cnt = 0

        watcher_status = defaultdict(int)
        non_running = defaultdict(list)
        for user_id, watcher in cls._get_watchers().items():
            watcher_status[watcher.stats.status.value] += 1
            if watcher.stats.status not in [WatcherStatus.WORKING]:
                non_running[watcher.stats.status.value].append((f"<code>{user_id}</code>", watcher.credentials.username))
            cnt += 1
        stats = {
            "count": cnt,
            "watcher_status": watcher_status,
            "non_running": non_running,
        }
        return stats



class BarsWatcherManager(WatcherManager):
    pass


class OsepWatcherManager(WatcherManager):
    pass