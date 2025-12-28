from datetime import datetime
from enum import Enum
import threading
from dataclasses import dataclass
from typing import Callable, Awaitable, Union

import aiohttp
import asyncio

from loguru import logger
from aiohttp import ClientTimeout, ConnectionTimeoutError, ClientConnectorError, ClientResponseError

from .notifier import ConnectionNotifier

from dataclasses import field


@dataclass
class ConnectionConfig:
    error_count: int = 0
    max_error_count: int = 3

    poll_interval: int = 30


class ConnectionStatus(Enum):
    CONNECTED = 'connected'
    DISCONNECTED = 'disconnected'

@dataclass
class ConnectionMetrics:
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED

    count_subscribers: int = 0

    start_time: datetime = field(default_factory=datetime.now)

    @property
    def is_connected(self) -> bool:
        return self.status == ConnectionStatus.CONNECTED

    @property
    def duration_time(self):
        return datetime.now() - self.start_time

class BaseConnectionMonitor:
    _instance = None
    _lock = threading.Lock()

    _url: str | None = None

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(BaseConnectionMonitor, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        if self._url is None:
            raise ValueError("Не была передана URL для проверки подключения")

        self._logger_template = f"{self.__class__.__name__} | "

        # важно: _get_session() использует self._session
        self._session: aiohttp.ClientSession | None = None
        self._session = self._get_session()

        self._check_task: asyncio.Task | None = None

        # Локальный импорт, чтобы избежать циклического импорта:
        # notifier -> base(ConnectionStatus)
        # base -> notifier(ConnectionNotifier)


        self._notifier = ConnectionNotifier(logger_template=self._logger_template)

        self._config = ConnectionConfig()
        self._metrics = ConnectionMetrics()

        self._initialized = True

    @property
    def url(self):
        return self._url


    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return self._session

    @property
    def metrics(self):
        return self._metrics
    async def _update_status(self, new_status: ConnectionStatus):
        old_status = self._metrics.status
        self._metrics.status = new_status

        if old_status != new_status:
            logger.info(self._logger_template + f"Статус соединения изменился: {old_status} -> {new_status}")
            asyncio.create_task(self._notifier.notify(new_status))

    async def check_connection(self) -> bool:
        try:
            session = self._get_session()
            async with session.get(url=self._url) as response:
                response.raise_for_status()
                if self._config.error_count != 0:
                    logger.info(self._logger_template + f"Подключение восстановлено | {self._config.error_count} / {self._config.max_error_count}")
                self._config.error_count = 0
                await self._update_status(ConnectionStatus.CONNECTED)
                return True

        except (ConnectionTimeoutError, ClientConnectorError, ClientResponseError) as e:
            self._config.error_count += 1

            logger.warning(self._logger_template + f"Ошибка подключения ({self._config.error_count} / {self._config.max_error_count}): {type(e).__name__}")

            if self._config.error_count >= self._config.max_error_count:
                await self._update_status(ConnectionStatus.DISCONNECTED)
            return False

        except Exception as error:
            logger.error(self._logger_template + f"Неизвестная ошибка при проверке соединения: {error}")
            self._config.error_count += 1
            if self._config.error_count >= self._config.max_error_count:
                await self._update_status(ConnectionStatus.DISCONNECTED)
            return False

    @property
    def is_connected(self) -> bool:
        return self._metrics.status == ConnectionStatus.CONNECTED

    async def _monitoring_loop(self):
        while True:
            try:
                await self.check_connection()
                await asyncio.sleep(self._config.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(self._logger_template + f"Ошибка в цикле мониторинга работоспособности: {e}")
                await asyncio.sleep(self._config.poll_interval)

    def subscribe(
        self,
        callback: Union[
            Callable[[ConnectionStatus], None],
            Callable[[ConnectionStatus], Awaitable[None]],
        ],
    ):
        if self._notifier.subscribe(callback):
            self._metrics.count_subscribers = self._notifier.count

    def unsubscribe(
        self,
        callback: Union[
            Callable[[ConnectionStatus], None],
            Callable[[ConnectionStatus], Awaitable[None]],
        ],
    ):
        if self._notifier.unsubscribe(callback):
            self._metrics.count_subscribers = self._notifier.count


    async def start_monitoring(self):
        if self._check_task is None or self._check_task.done():
            self._check_task = asyncio.create_task(self._monitoring_loop())


    async def stop_monitoring(self):
        if self._check_task and not self._check_task.done():
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                logger.debug(self._logger_template + "Задача мониторинга отменена")
            logger.info(self._logger_template + f"Мониторинг {self._url} приостановлен")

        if self._session and not self._session.closed:
            try:
                await self._session.close()
            except Exception as e:
                logger.error(self._logger_template + f"Ошибка при закрытии сессии: {e}")


class BarsConnectionMonitor(BaseConnectionMonitor):
    _url = "https://bars.mpei.ru/bars_web/"

class OsepConnectionMonitor(BaseConnectionMonitor):
    _url = "https://mail.mpei.ru/CookieAuth.dll?GetLogon?curl=Z2F&reason=0&formdir=2"

class TestConnectionMonitor(BaseConnectionMonitor):
    _url = "https://example.com"