import asyncio
from datetime import datetime, timedelta

import aiohttp
from aiohttp import ConnectionTimeoutError, ClientConnectorError, ClientResponseError

from typing import List, Callable, Awaitable, Optional
from watchers.models.connection_monitor_models import ConnectionStatus, ConnectionMetrics, ConnectionMonitorConfig
from loguru import logger

from watchers.core.event_service import EventService





class BaseConnectionMonitor(EventService):
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

        self._session: aiohttp.ClientSession | None = None
        self._session = self._get_session()
        self._config = ConnectionMonitorConfig(url=self._url)

        self._check_task: asyncio.Task | None = None

        self._metrics = ConnectionMetrics()

        self._initialized = True

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
            self.notify_subscribers(new_status)
            # asyncio.create_task(self.notify_subscribers(new_status))

    async def check_connection(self) -> bool:
        try:
            async with self.session.get(url=self._url) as response:
                response.raise_for_status()
                if self._metrics.error_count != 0:
                    logger.info(self._logger_template + f"Подключение восстановлено |  Не успешных подключений было: {self._metrics.error_count}")
                self._metrics.error_count = 0
                await self._update_status(ConnectionStatus.CONNECTED)
                return True

        except (ConnectionTimeoutError, ClientConnectorError, ClientResponseError) as e:
            self._metrics.error_count += 1
            self._metrics.last_error_time = datetime.now()
            # if self._metrics.error_count <= self._config.max_error_count:
            # logger.warning(self._logger_template + f"Ошибка подключения ({self._metrics.error_count} / {self._config.max_error_count}): {type(e).__name__}")
            if self._metrics.error_count <= self._config.max_error_count:
                logger.warning(
                    self._logger_template + f"Ошибка подключения ({self._metrics.error_count} / {self._config.max_error_count}): {type(e).__name__}")
            else:
                # Логируем и после превышения лимита
                logger.debug(
                    self._logger_template + f"Ошибка подключения ({self._metrics.error_count}): {type(e).__name__}")

            if self._metrics.error_count >= self._config.max_error_count:
                await self._update_status(ConnectionStatus.DISCONNECTED)
            return False


        except Exception as error:
            # raise
            logger.error(self._logger_template + f"Неизвестная ошибка при проверке соединения: {error}")
            self._metrics.error_count += 1
            self._metrics.last_error_time = datetime.now()
            if self._metrics.error_count >= self._config.max_error_count:
                await self._update_status(ConnectionStatus.DISCONNECTED)
            return False

    @property
    def is_connected(self) -> bool:
        return self._metrics.status == ConnectionStatus.CONNECTED

    async def _monitoring_loop(self):
        while True:
            try:
                a = await self.check_connection()
                await asyncio.sleep(self._config.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                # raise
                # logger.exception(e)
                # print(self._logger_template + f"Ошибка в цикле мониторинга {self.__name__}: {e}")
                logger.error(self._logger_template + f"Ошибка в цикле мониторинга {self.__name__}: {e}")
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
                logger.debug(self._logger_template + "Задача мониторинга отменена")
            logger.info(self._logger_template + f"Мониторинг {self._url} остановлен")

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