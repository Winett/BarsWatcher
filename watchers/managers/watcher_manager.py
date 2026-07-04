import asyncio
import random
from typing import Dict, Optional, Generic, TypeVar
from abc import ABC
from collections import defaultdict

from loguru import logger

from watchers.core.event_service import EventService
from watchers.core.base_watcher import BaseWatcher
from watchers.models.watcher_models import (
    UserCredentials, WatcherType, WatcherEvent, EventType, WatcherStatus
)
from watchers.models.connection_monitor_models import ConnectionStatus
from watchers.models.mail_models import AttachmentData
from watchers.services.notification_service import TelegramNotificationService
from watchers.core.exceptions import AuthError, DataParsingError, ResponseError, RequestVerificationTokenError, Auth2FA

from services.user import UserService
from database.db import async_session
from settings import settings
from uuid import uuid4

W = TypeVar('W', bound=BaseWatcher)


class WatcherManager(ABC, Generic[W]):
    """Единый менеджер вотчеров с generic-типизацией."""

    _managers_watchers: dict[str, dict[int, BaseWatcher]] = defaultdict(dict)
    notification_service = TelegramNotificationService()

    # Конфигурация staggered resume
    STAGGER_DELAY = 2.0    # Базовая задержка между вотчерами (сек)
    STAGGER_JITTER = 3.0   # Случайный разброс (сек)

    @classmethod
    def _get_watchers(cls) -> dict[int, W]:
        return cls._managers_watchers[cls.__name__]

    @classmethod
    async def process_connection_event(cls, conn_event: ConnectionStatus):
        """Обработка события соединения от ConnectionMonitor."""
        logger.info(f"{cls.__name__} | Событие соединения: {conn_event.value}")

        match conn_event:
            case ConnectionStatus.CONNECTED:
                # Сервер восстановился — staggered resume
                await cls._staggered_resume()
            case ConnectionStatus.DEGRADED:
                # Сервер медленный — просто логируем, вотчеры продолжают
                logger.warning(f"{cls.__name__} | Сервер работает медленно (DEGRADED)")
            case ConnectionStatus.DISCONNECTED:
                # Сервер недоступен — ставим event unavailable + пауза
                cls._set_all_server_unavailable()
                await cls.pause_all()
            case ConnectionStatus.RECOVERING:
                # Сервер восстанавливается — можно пробовать
                logger.info(f"{cls.__name__} | Сервер восстанавливается (RECOVERING)")
            case _:
                logger.debug(f"{cls.__name__} | Статус: {conn_event.value}")

    @classmethod
    def _set_all_server_unavailable(cls):
        """Установить event unavailable для всех вотчеров."""
        for watcher in cls._get_watchers().values():
            watcher.on_server_unavailable()

    @classmethod
    def _set_all_server_available(cls):
        """Установить event available для всех вотчеров."""
        for watcher in cls._get_watchers().values():
            watcher.on_server_available()

    @classmethod
    async def _staggered_resume(cls):
        """Возобновление вотчеров с задержками для предотвращения thundering herd."""
        watchers = list(cls._get_watchers().values())
        if not watchers:
            return

        # Сначала ставим event available
        cls._set_all_server_available()

        # Случайный порядок для равномерного распределения
        random.shuffle(watchers)

        logger.info(f"{cls.__name__} | Staggered resume: {len(watchers)} вотчеров")

        for i, watcher in enumerate(watchers):
            delay = cls.STAGGER_DELAY + random.uniform(0, cls.STAGGER_JITTER)
            logger.info(
                f"{cls.__name__} | Resume {watcher.credentials.username} "
                f"через {delay:.1f}s ({i+1}/{len(watchers)})"
            )
            await asyncio.sleep(delay)
            await watcher.resume()

        logger.info(f"{cls.__name__} | Staggered resume завершён")

    @classmethod
    def register_watcher(cls, user_id: int, watcher: W):
        cls._get_watchers()[user_id] = watcher
        watcher.subscribe(cls._handle_watcher_event)

    @classmethod
    async def register_watcher_and_start(cls, user_id: int, watcher: W):
        cls.register_watcher(user_id, watcher)
        await watcher.start()

    @classmethod
    def unregister_watcher(cls, user_id: int):
        watcher = cls._get_watchers().pop(user_id, None)
        if watcher:
            watcher.unsubscribe(cls._handle_watcher_event)

    @classmethod
    def get_watcher_instance(cls, user_id: int) -> Optional[W]:
        return cls._get_watchers().get(user_id, None)

    @classmethod
    async def _handle_watcher_event(cls, event: WatcherEvent):
        """Обработка событий от вотчеров"""
        logger.info(
            f"{cls.__name__} | {event.username} ({event.user_id}) | "
            f"Событие: {event.event_type.value} | {event.watcher_type.value}"
        )
        match event.event_type:
            case EventType.NEW_CHANGE:
                files_count = len(event.metadata.get('files', []))
                logger.info(
                    f"{cls.__name__} | {event.username} | "
                    f"Новое изменение | files={files_count} | "
                    f"{event.message[:100]}..."
                )
                await cls.notification_service.send_message_with_documents(
                    event.user_id,
                    event.message,
                    files=event.metadata.get('files', [])
                )
                logger.debug(f"{cls.__name__} | {event.username} | Уведомление отправлено")
            case EventType.EXCEPTION:
                logger.error(
                    f"{cls.__name__} | {event.username} | "
                    f"Ошибка: {type(event.error).__name__}: {event.error}"
                )
                match event.error:
                    case error if isinstance(error, (AuthError, Auth2FA)):
                        if isinstance(error, Auth2FA):
                            message = "Нужно переавторизоваться"
                        else:
                            message = "Неверный логин или пароль"
                        logger.warning(f"{cls.__name__} | {event.username} | Фатальная ошибка: {message}")
                        await cls.notification_service.send_message(
                            event.user_id,
                            f" [{event.watcher_type.value}] {message}"
                        )
                        await cls.stop_and_delete(event.user_id)
                        async with async_session() as session:
                            if event.watcher_type == WatcherType.BARS:
                                await UserService(session).set_bars_status_used(event.user_id, False)
                            elif event.watcher_type == WatcherType.OSEP:
                                await UserService(session).set_osep_status_used(event.user_id, False)
                        logger.info(f"{cls.__name__} | {event.username} | Вотчер остановлен, статус сброшен")
                    case error:
                        if isinstance(error, (DataParsingError, ResponseError, RequestVerificationTokenError)):
                            uid = uuid4().hex
                            content = error.content.encode(errors="ignore", encoding="utf-8") if isinstance(error.content, str) else error.content
                            att_data = AttachmentData(
                                id=uid,
                                content_type="application/html",
                                filename=f"{event.username}_{event.user_id}_{event.watcher_type.value}.html",
                                size=len(content),
                                content=content,
                            )
                            logger.info(f"{cls.__name__} | {event.username} | Контент ошибки отправлен админу ({len(content)} bytes)")
                            for admin in settings.admins:
                                await cls.notification_service.send_message_with_documents(
                                    admin,
                                    f"Ошибка при обработке запроса: {type(error).__name__} у {event.username} <code>{event.user_id}</code>",
                                    files=[att_data]
                                )
                        logger.exception(error)
                        logger.info(f"{cls.__name__} | {event.username} | Перезапуск через 5 сек...")
                        await asyncio.sleep(5)
                        try:
                            await cls.get_watcher_instance(event.user_id).restart()
                            logger.info(f"{cls.__name__} | {event.username} | Вотчер перезапущен")
                        except AttributeError:
                            logger.warning(f"{cls.__name__} | {event.username} | Вотчер не найден для перезапуска")
            case _:
                logger.warning(f"{cls.__name__} Неизвестное событие: {event.event_type}")

    @classmethod
    def _get_all_not_started_instance(cls) -> list[W]:
        return [w for w in cls._get_watchers().values() if not w.is_running]

    @classmethod
    async def pause_all(cls):
        logger.info(f"{cls.__name__} | Пауза всех вотчеров")
        cnt = 0
        for w in cls._get_watchers().values():
            await w.pause()
            cnt += 1
        logger.info(f"{cls.__name__} | Пауза {cnt} вотчеров")

    @classmethod
    async def resume_all(cls):
        logger.info(f"{cls.__name__} | Возобновление всех вотчеров")
        cnt = 0
        for w in cls._get_watchers().values():
            await w.resume()
            cnt += 1
        logger.info(f"{cls.__name__} | Возобновление {cnt} вотчеров")

    @classmethod
    async def start_all(cls):
        instances = cls._get_all_not_started_instance()
        logger.debug(f"{cls.__name__} Запуск {len(instances)} вотчеров")
        for w in instances:
            await w.start()

    @classmethod
    async def stop_all(cls):
        for w in cls._get_watchers().values():
            await w.stop()

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
        for user_id, w in cls._get_watchers().items():
            watcher_status[w.stats.status.value] += 1
            if w.stats.status not in [WatcherStatus.WORKING]:
                non_running[w.stats.status.value].append(
                    (f"<code>{user_id}</code>", w.credentials.username)
                )
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
