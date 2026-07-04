from abc import ABC, abstractmethod
import asyncio
from datetime import datetime
from typing import Optional, Callable, Awaitable
from loguru import logger

from watchers.models.watcher_models import (
    WatcherEvent, WatcherStats, WatcherConfig,
    WatcherStatus, EventType
)

from watchers.models.watcher_models import UserCredentials
from watchers.core.event_service import EventService
from watchers.core.exceptions import AuthError, Auth2FA
from watchers.services.cache_service import AsyncFileCacher


class BaseWatcher(ABC):
    def __init__(
            self,
            credentials: UserCredentials,
            cache_service: AsyncFileCacher,
            config: Optional[WatcherConfig] = None
    ):
        self.credentials = credentials
        self.cache_service = cache_service
        self.config = config or WatcherConfig()

        self._stats = WatcherStats()
        self._is_running = False
        self._is_pausing = False
        self._task: Optional[asyncio.Task] = None

        # asyncio.Event: True = сервер доступен, False = недоступен
        self._server_available = asyncio.Event()
        self._server_available.set()  # По умолчанию доступен

        self._logger_template = f"{self.__class__.__name__:^10} | {credentials.username:^10} | "

        self._register_instance()
        logger.debug(f"{self._logger_template} Инициализирован | poll_interval={self.config.poll_interval}s")

    def __repr__(self):
        return f"{self.__class__.__name__}(creds={self.credentials})"

    @abstractmethod
    def _register_instance(self):
        pass

    @abstractmethod
    async def fetch_data(self):
        """Получение одного снимка данных из источника"""
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
        logger.info(f"{self._logger_template} Цикл запущен | poll_interval={self.config.poll_interval}s")
        self._is_running = True
        self._stats.status = WatcherStatus.WORKING
        iteration = 0

        try:
            while self._is_running:
                # Ждём доступности сервера (если недоступен)
                if not self._server_available.is_set():
                    logger.info(f"{self._logger_template} Сервер недоступен, ожидание...")
                    await self._server_available.wait()
                    logger.info(f"{self._logger_template} Сервер доступен, возобновление работы")

                iteration += 1
                logger.debug(f"{self._logger_template} ── Итерация #{iteration} ──")
                try:
                    await self._iteration()
                except asyncio.CancelledError:
                    raise
                except (AuthError, Auth2FA) as e:
                    logger.error(f"{self._logger_template} Фатальная ошибка авторизации: {type(e).__name__}: {e}")
                    await self._handle_error(e)
                    break
                except Exception as e:
                    logger.error(f"{self._logger_template} Ошибка итерации #{iteration}: {type(e).__name__}: {e}")
                    await self._handle_error(e)
                logger.debug(f"{self._logger_template} Итерация #{iteration} завершена | sleep {self.config.poll_interval}s")
                await asyncio.sleep(self.config.poll_interval)
        except asyncio.CancelledError:
            logger.debug(f"{self._logger_template} Задача отменена")
        finally:
            await self._cleanup()
            logger.info(f"{self._logger_template} Цикл завершён | всего итераций: {iteration}")

    async def _iteration(self):
        """Одна итерация проверки: fetch → process → detect"""
        logger.debug(f"{self._logger_template} [1/3] fetch_data...")
        data = await self.fetch_data()
        if isinstance(data, bytes):
            data = data.decode()
        logger.debug(f"{self._logger_template} [1/3] fetch_data OK | size={len(str(data))} chars")

        logger.debug(f"{self._logger_template} [2/3] process_data...")
        processed = await self.process_data(data)
        logger.debug(f"{self._logger_template} [2/3] process_data OK")

        logger.debug(f"{self._logger_template} [3/3] detect_changes...")
        await self._check_for_changes(processed)
        self._stats.last_fetch_time = datetime.now()
        logger.debug(f"{self._logger_template} [3/3] detect_changes OK")

    async def _check_for_changes(self, new_data):
        """Проверка изменений и отправка уведомлений"""
        cache_key = f"{self.__class__.__name__}_{self.credentials.username}"

        old_data = await self.cache_service.get(cache_key)
        logger.debug(f"{self._logger_template} Кэш: {'есть данные' if old_data else 'пусто (первая итерация)'}")

        changes = await self.detect_changes(old_data, new_data)

        await self.cache_service.set(cache_key, new_data, self.config.cache_ttl)
        logger.debug(f"{self._logger_template} Кэш обновлён | ttl={self.config.cache_ttl}s")

        if changes:
            logger.info(f"{self._logger_template} Обнаружено изменений: {len(changes)}")
            for i, change in enumerate(changes, 1):
                logger.info(f"{self._logger_template}   [{i}] {change}")
            await self._notify_changes(changes)
        else:
            logger.debug(f"{self._logger_template} Изменений нет")

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

        logger.debug(f"{self._logger_template} Отправка уведомления подписчикам...")
        await self._notify_subscribers(event)
        logger.debug(f"{self._logger_template} Уведомление отправлено")

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

        logger.warning(
            f"{self._logger_template} Ошибка #{self._stats.error_count}: "
            f"{type(error).__name__}: {error}"
        )

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
        EventService().notify_subscribers(event)

    async def _cleanup(self):
        """Очистка ресурсов"""
        self._is_running = False
        if not self._is_pausing:
            self._stats.status = WatcherStatus.STOPPED
        self._is_pausing = False

    def on_server_available(self):
        """Вызывается когда сервер становится доступным."""
        logger.debug(f"{self._logger_template} Сервер доступен → event.set()")
        self._server_available.set()

    def on_server_unavailable(self):
        """Вызывается когда сервер становится недоступным."""
        logger.debug(f"{self._logger_template} Сервер недоступен → event.clear()")
        self._server_available.clear()

    async def restart(self):
        logger.info(f"{self._logger_template} Перезапуск")
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
            logger.debug(f"{self._logger_template} Уже запущен, пропуск")
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
        self._is_pausing = True
        self._is_running = False
        self._stats.status = WatcherStatus.PAUSED

    async def resume(self):
        """Возобновление работы"""
        logger.info(f"{self._logger_template} Возобновление")
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = asyncio.create_task(self.run())

    def subscribe(self, handler: Callable[[WatcherEvent], Awaitable[None]]):
        """Подписка на события"""
        EventService().subscribe(handler)

    def unsubscribe(self, handler: Callable[[WatcherEvent], Awaitable[None]]):
        """Отписка от событий"""
        EventService().unsubscribe(handler)

    @property
    def stats(self) -> WatcherStats:
        return self._stats

    @property
    def is_running(self) -> bool:
        return self._is_running

    async def close(self):
        ...
