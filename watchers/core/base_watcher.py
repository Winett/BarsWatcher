from abc import ABC, abstractmethod
import asyncio
from datetime import datetime
from typing import Optional, Callable, Awaitable
from loguru import logger

from watchers.models.watcher_models import (
    WatcherEvent, WatcherStats, WatcherConfig,
    WatcherStatus, EventType, WatcherType
)

from watchers.models.watcher_models import UserCredentials
from watchers.services.event_service import EventService
from watchers.services.cache_service import AsyncFileCacher
from watchers.services.fetcher_service import BaseFetcherService
from watchers.services.notification_service import BaseNotificationService


class BaseWatcher(ABC):
    def __init__(
            self,
            credentials: UserCredentials,
            # fetcher_service: BaseFetcherService,
            cache_service: AsyncFileCacher,
            # notification_service: BaseNotificationService,
            config: Optional[WatcherConfig] = None
    ):
        self.credentials = credentials
        # self.fetcher_service = fetcher_service
        self.cache_service = cache_service
        self.event_service = EventService()
        # self.notification_service = notification_service
        self.config = config or WatcherConfig()

        self._stats = WatcherStats()
        self._is_running = False
        self._task: Optional[asyncio.Task] = None
        # self._event_handlers: list[Callable[[WatcherEvent], Awaitable[None]]] = []

        self._logger_template = f"{self.__class__.__name__} | {credentials.username} | "

        self._register_instance()

    def __repr__(self):
        return f"{self.__class__.__name__}(creds={self.credentials})"

    @abstractmethod
    def _register_instance(self):
        pass

    @abstractmethod
    async def fetch_data(self):
        """Получение данных из источника"""
        pass

    @abstractmethod
    async def process_data(self, data):
        """Обработка полученных данных"""
        pass

    @abstractmethod
    async def detect_changes(self, old_data, new_data):
        """Обнаружение изменений в данных"""
        pass

    async def run(self):
        """Основной цикл вотчера"""
        self._is_running = True
        self._stats.status = WatcherStatus.WORKING

        try:
            while self._is_running:
                await self._iteration()
                await asyncio.sleep(self.config.poll_interval)
        except asyncio.CancelledError:
            logger.debug(f"{self._logger_template} Отменен")
        except Exception as e:
            await self._handle_error(e)
        finally:
            await self._cleanup()

    async def _iteration(self):
        """Одна итерация проверки"""
        # try:
        data = await self.fetch_data()
        data = data.decode()
        processed = await self.process_data(data)
        await self._check_for_changes(processed)
        self._stats.last_fetch_time = datetime.now()
        # except Exception as e:
        #     await self._handle_error(e)
        #     raise

    async def _check_for_changes(self, new_data):
        """Проверка изменений и отправка уведомлений"""
        cache_key = f"{self.__class__.__name__}_{self.credentials.username}"

        old_data = await self.cache_service.get(cache_key)
        changes = await self.detect_changes(old_data, new_data)

        await self.cache_service.set(cache_key, new_data, self.config.cache_ttl)

        if changes:

            await self._notify_changes(changes)

    async def _notify_changes(self, changes: list[str]):
        """Отправка уведомлений об изменениях"""
        event = WatcherEvent(
            event_type=EventType.NEW_CHANGE,
            user_id=self.credentials.user_id,
            username=self.credentials.username,
            status=self._stats.status,
            watcher_type=self.credentials.watcher_type,
            message="\n".join(changes)
        )

        # await self.notification_service.send_notification(event)
        await self._notify_subscribers(event)

    def _generator_events(self, event_type: EventType, message: str, **metadata):
        return WatcherEvent(
            event_type=event_type,
            user_id=self.credentials.user_id,
            username=self.credentials.username,
            status=self._stats.status,
            message=message,
            watcher_type=self.credentials.watcher_type,
            metadata=metadata,
        )

    async def _handle_error(self, error: Exception):
        """Обработка ошибок"""
        self._stats.error_count += 1
        self._stats.last_error_time = datetime.now()
        self._stats.status = WatcherStatus.ERROR

        # logger.error(f"{self._logger_template} Ошибка: {error}")
        # logger.exception(error)

        event = WatcherEvent(
            event_type=EventType.EXCEPTION,
            user_id=self.credentials.user_id,
            username=self.credentials.username,
            status=self._stats.status,
            watcher_type=self.credentials.watcher_type,
            message=f"Ошибка: {str(error)}",
            error=error
        )

        await self._notify_subscribers(event)

    async def _notify_subscribers(self, event: WatcherEvent):
        self.event_service.notify_subscribers(event)

    # async def _dispatch_event(self, event: WatcherEvent):
    #     """Отправка события всем обработчикам"""
    #     for handler in self._event_handlers:
    #         try:
    #             if asyncio.iscoroutinefunction(handler):
    #                 await handler(event)
    #             else:
    #                 handler(event)
    #         except Exception as e:
    #             logger.error(f"Ошибка в обработчике событий: {e}")

    async def _cleanup(self):
        """Очистка ресурсов"""
        self._is_running = False
        self._stats.status = WatcherStatus.STOPPED
        # await self.fetcher_service.close()
        # await self.auth_service.logout()

    async def restart(self):
        logger.info(self._logger_template + f"Перезапуск")
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self.run())
        else:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = asyncio.create_task(self.run())



    async def start(self):
        """Запуск вотчера"""
        if self._task and not self._task.done():
            return

        logger.info(f"{self._logger_template} Запуск")
        self._task = asyncio.create_task(self.run())

    async def stop(self):
        """Остановка вотчера"""
        logger.info(f"{self._logger_template} Остановка")
        self._is_running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def pause(self):
        """Пауза вотчера"""
        logger.info(f"{self._logger_template} Пауза")
        self._is_running = False
        self._stats.status = WatcherStatus.PAUSED

    async def resume(self):
        """Возобновление работы"""
        logger.info(f"{self._logger_template} Возобновление")
        await self.start()

    def subscribe(self, handler: Callable[[WatcherEvent], Awaitable[None]]):
        """Подписка на события"""
        self.event_service.subscribe(handler)

    def unsubscribe(self, handler: Callable[[WatcherEvent], Awaitable[None]]):
        """Отписка от событий"""
        self.event_service.unsubscribe(handler)

    @property
    def stats(self) -> WatcherStats:
        return self._stats

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def close(self):
        ...