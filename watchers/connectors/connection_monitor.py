import asyncio
import random
from datetime import datetime

import aiohttp
from aiohttp import ConnectionTimeoutError, ClientConnectorError, ClientResponseError

from watchers.models.connection_monitor_models import (
    ConnectionStatus, ConnectionMetrics, ConnectionMonitorConfig
)
from loguru import logger

from watchers.core.event_service import EventService


class BaseConnectionMonitor(EventService):
    """Мониторинг доступности сервера с state machine и asyncio.Event.

    Состояния:
        UNKNOWN → CONNECTED (первый OK)
        UNKNOWN → DISCONNECTED (первый FAIL)
        CONNECTED → DISCONNECTED (failure_threshold FAIL подряд)
        CONNECTED → DEGRADED (slow_threshold × degraded_checks медленно)
        DEGRADED → CONNECTED (быстрый OK)
        DEGRADED → DISCONNECTED (FAIL)
        DISCONNECTED → RECOVERING (1 OK)
        RECOVERING → CONNECTED (recovery_threshold OK подряд)
        RECOVERING → DISCONNECTED (FAIL)
    """

    _instance = None
    _url: str | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BaseConnectionMonitor, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        if self._url is None:
            raise ValueError("Не была передана URL для проверки подключения")
        super().__init__()
        self._logger_template = f"{self.__class__.__name__} | "

        self._config = ConnectionMonitorConfig(url=self._url)

        self._session: aiohttp.ClientSession | None = None
        self._session = self._get_session()

        self._check_task: asyncio.Task | None = None

        self._metrics = ConnectionMetrics()

        # asyncio.Event: True = сервер доступен, False = недоступен
        self._available_event = asyncio.Event()
        self._available_event.set()  # По умолчанию доступен

        self._initialized = True
        logger.debug(f"{self._logger_template} Инициализирован | url={self._url}")

    @property
    def url(self):
        return self._url

    @property
    def session(self):
        if not self._session or self._session.closed:
            self._session = self._get_session()
        return self._session

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._config.timeout)
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self._session

    @property
    def metrics(self):
        return self._metrics

    @property
    def is_connected(self) -> bool:
        return self._metrics.status in (ConnectionStatus.CONNECTED, ConnectionStatus.DEGRADED)

    @property
    def is_available(self) -> bool:
        """asyncio.Event: True если сервер доступен (CONNECTED или DEGRADED)."""
        return self._available_event.is_set()

    async def _update_status(self, new_status: ConnectionStatus):
        old_status = self._metrics.status
        self._metrics.status = new_status

        if old_status != new_status:
            logger.info(
                f"{self._logger_template} Статус: {old_status.value} → {new_status.value}"
            )

            # Управление asyncio.Event
            if new_status in (ConnectionStatus.CONNECTED, ConnectionStatus.DEGRADED):
                self._available_event.set()
            elif new_status == ConnectionStatus.DISCONNECTED:
                self._available_event.clear()
            # RECOVERING не блокирует — вотчеры могут пробовать

            self.notify_subscribers(new_status)

    async def check_connection(self) -> bool:
        """Одна проверка соединения. Возвращает True если OK."""
        self._metrics.total_checks += 1
        start_time = datetime.now()

        try:
            async with self.session.get(url=self._url) as response:
                response.raise_for_status()
                elapsed = (datetime.now() - start_time).total_seconds()
                self._metrics.last_response_time = elapsed
                self._metrics.last_success_time = datetime.now()

                # Сброс счётчиков ошибок
                if self._metrics.error_count > 0:
                    logger.info(
                        f"{self._logger_template} Подключение восстановлено "
                        f"(было {self._metrics.error_count} ошибок подряд)"
                    )
                self._metrics.error_count = 0
                self._metrics.success_count += 1

                # Проверка на медленный ответ
                is_slow = elapsed > self._config.slow_threshold

                if is_slow:
                    self._metrics.slow_count += 1
                    self._metrics.total_errors += 1
                    logger.warning(
                        f"{self._logger_template} Медленный ответ: {elapsed:.1f}s "
                        f"(порог: {self._config.slow_threshold}s) "
                        f"| медленных подряд: {self._metrics.slow_count}/{self._config.degraded_checks}"
                    )

                    # Проверка на DEGRADED
                    if self._metrics.slow_count >= self._config.degraded_checks:
                        await self._update_status(ConnectionStatus.DEGRADED)
                    elif self._metrics.status != ConnectionStatus.DEGRADED:
                        await self._update_status(ConnectionStatus.CONNECTED)
                else:
                    self._metrics.slow_count = 0

                    # Восстановление из DISCONNECTED → RECOVERING
                    if self._metrics.status == ConnectionStatus.DISCONNECTED:
                        self._metrics.success_count = 1
                        await self._update_status(ConnectionStatus.RECOVERING)
                    # Продолжение восстановления из RECOVERING
                    elif self._metrics.status == ConnectionStatus.RECOVERING:
                        if self._metrics.success_count >= self._config.recovery_threshold:
                            await self._update_status(ConnectionStatus.CONNECTED)
                        else:
                            logger.debug(
                                f"{self._logger_template} RECOVERING: "
                                f"{self._metrics.success_count}/{self._config.recovery_threshold} OK"
                            )
                    # DEGRADED → CONNECTED (быстрый ответ)
                    elif self._metrics.status == ConnectionStatus.DEGRADED:
                        await self._update_status(ConnectionStatus.CONNECTED)
                    else:
                        await self._update_status(ConnectionStatus.CONNECTED)

                return True

        except (ConnectionTimeoutError, ClientConnectorError, ClientResponseError) as e:
            self._metrics.error_count += 1
            self._metrics.success_count = 0
            self._metrics.total_errors += 1
            self._metrics.last_error_time = datetime.now()
            self._metrics.slow_count = 0

            if self._metrics.error_count <= self._config.failure_threshold:
                logger.warning(
                    f"{self._logger_template} Ошибка ({self._metrics.error_count}/{self._config.failure_threshold}): "
                    f"{type(e).__name__}"
                )
            else:
                logger.debug(
                    f"{self._logger_template} Ошибка ({self._metrics.error_count}): {type(e).__name__}"
                )

            # Проверка на DISCONNECTED
            if self._metrics.status == ConnectionStatus.RECOVERING:
                # Из RECOVERING любая ошибка → сразу DISCONNECTED
                await self._update_status(ConnectionStatus.DISCONNECTED)
            elif self._metrics.error_count >= self._config.failure_threshold:
                if self._metrics.status != ConnectionStatus.DISCONNECTED:
                    await self._update_status(ConnectionStatus.DISCONNECTED)
            elif self._metrics.status == ConnectionStatus.UNKNOWN:
                pass

            return False

        except Exception as error:
            self._metrics.error_count += 1
            self._metrics.success_count = 0
            self._metrics.total_errors += 1
            self._metrics.last_error_time = datetime.now()
            self._metrics.slow_count = 0

            logger.error(f"{self._logger_template} Неизвестная ошибка: {error}")

            if self._metrics.status == ConnectionStatus.RECOVERING:
                await self._update_status(ConnectionStatus.DISCONNECTED)
            elif self._metrics.error_count >= self._config.failure_threshold:
                await self._update_status(ConnectionStatus.DISCONNECTED)

            return False

    async def _monitoring_loop(self):
        logger.info(f"{self._logger_template} Мониторинг запущен | poll={self._config.poll_interval}s")
        while True:
            try:
                await self.check_connection()
                await asyncio.sleep(self._config.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"{self._logger_template} Ошибка в цикле мониторинга: {e}")
                await asyncio.sleep(self._config.poll_interval)

    async def start_monitoring(self):
        if self._check_task is None or self._check_task.done():
            self._check_task = asyncio.create_task(self._monitoring_loop())

    async def stop_monitoring(self):
        if self._check_task and not self._check_task.done():
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                logger.debug(f"{self._logger_template} Задача мониторинга отменена")
            logger.info(f"{self._logger_template} Мониторинг остановлен")

        await self.close()

    async def close(self):
        super().close()
        if self._session and not self._session.closed:
            await self._session.close()
            if self._session.connector and not self._session.connector.closed:
                await self._session.connector.close()


class BarsMonitor(BaseConnectionMonitor):
    _url = "https://bars.mpei.ru/bars_web/"


class OsepMonitor(BaseConnectionMonitor):
    _url = "https://mail.mpei.ru/CookieAuth.dll?GetLogon?curl=Z2FowaZ2FZ3FbOZ3D1&reason=0&formdir=2#path=/mail"
