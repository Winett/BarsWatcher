from abc import ABC, abstractmethod
from .auth.base import BaseAuth

from typing import Type, Union, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import asyncio

from watchers.connection.base import BaseConnectionMonitor

from loguru import logger

from .fetcher.base import BaseFetcher
from .fetcher.exceptions import AuthError
from .parser.base import BaseParser
from watchers.notifier.watcher_notifier import WatcherNotifier
from watchers.connection.base import ConnectionStatus

class WatcherStatus(Enum):
    WORKING = "WORKING"
    ERROR = "ERROR"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"

@dataclass
class WatcherEvent:
    user_id: int
    username: str
    status: WatcherStatus
    watcher_name: str
    error: Exception | None = None
    datetime: datetime = field(default_factory=datetime.now)





@dataclass
class WatcherStats:
    start_time: datetime = field(default_factory=datetime.now)

    last_fetch_time: datetime | None = None  # None - если ещё не было fetch
    status: WatcherStatus = WatcherStatus.STOPPED

    last_auth_time: datetime | None = None  # None - если ещё не было auth

    error_counts: int = 0
    last_error_time: datetime | None = None

    # detected_changes: int = 0
    # last_detected_change_time: datetime = 0

    @property
    def is_healthy(self):
        return self.status == WatcherStatus.WORKING

    @property
    def duration_time(self):
        return datetime.now() - self.start_time

@dataclass
class WatcherConfig:
    working: bool = False
    poll_interval: int = 60

    base_error_cooldown: int = 60
    error_cooldown: int = 60

    max_error_cooldown: int = 60 * 15
    error_cooldown_multiplier: int = 2
    timeout: int = 30


    @property
    def error_cooldown_now(self):
        self.error_cooldown = min(self.error_cooldown * self.error_cooldown_multiplier, self.max_error_cooldown)
        return self.error_cooldown

    def reset_error_cooldown(self):
        self.error_cooldown = self.base_error_cooldown




class BaseWatcher(ABC):

    def __init__(self, username, password, user_id, auth_class: Type[BaseAuth], fetcher_class: Type[BaseFetcher], connection_class: Type[BaseConnectionMonitor], parser_class: Type[BaseParser] = None):
        self.auth = auth_class(username, password)
        self.fetcher = fetcher_class(self.auth.get_session, self.auth.login)
        self.parser = parser_class

        self.connection: BaseConnectionMonitor = connection_class()

        self.username = username
        self.password = password
        self.user_id = user_id
        self._logger_template = f"{self.__class__.__name__} | {self.username} | "

        self._config = WatcherConfig()
        self._stats = WatcherStats()
        self._run_task: asyncio.Task | None = None

        self._notifier = WatcherNotifier(logger_template=self._logger_template)

    @abstractmethod
    async def _fetch_and_process_data(self):
        ...

    @abstractmethod
    async def test_login(self):
        ...

    async def on_change_status_connection(self, new_status: ConnectionStatus):
        if new_status == ConnectionStatus.CONNECTED:
            self._config.working = True
            self._stats.status = WatcherStatus.WORKING
            # логичнее запускать задачу, если её нет или она уже завершилась
            if self._run_task is None or self._run_task.done():
                self._enable_run_task(self.run)

        elif new_status == ConnectionStatus.DISCONNECTED:
            logger.warning(self._logger_template + "Остановка из-за отсутствия подключения к серверу")
            self._config.working = False
            self._stats.status = WatcherStatus.PAUSED

            task = self._run_task
            if task is not None and not task.done():
                task.cancel()

    async def _check_on_error_watcher(self, task: asyncio.Task):
        try:
            task.result()

            event = WatcherEvent(
                status=WatcherStatus.STOPPED,
                error=None,
                watcher_name=self.__class__.__name__,
                datetime=datetime.now(),
                user_id=self.user_id,
                username=self.username
            )

        except asyncio.CancelledError:
            #Не факт, что status=PAUSED
            event = WatcherEvent(
                status=WatcherStatus.PAUSED,
                error=None,
                watcher_name=self.__class__.__name__,
                datetime=datetime.now(),
                user_id=self.user_id,
                username=self.username
            )

        except AuthError as e:
            event = WatcherEvent(
                status=WatcherStatus.ERROR,
                error=e,
                watcher_name=self.__class__.__name__,
                datetime=datetime.now(),
                user_id=self.user_id,
                username=self.username
            )

        except Exception as e:
            event = WatcherEvent(
                status=WatcherStatus.ERROR,
                error=e,
                watcher_name=self.__class__.__name__,
                datetime=datetime.now(),
                user_id=self.user_id,
                username=self.username
            )


        await self._notifier.notify(event)

    async def run(self) -> None:
        try:
            while self._config.working:
                await self._fetch_and_process_data()
                self._stats.last_fetch_time = datetime.now()

                await asyncio.sleep(self._config.poll_interval)

        except asyncio.CancelledError:
            logger.debug(f"{self._logger_template} Отмена задачи run")
            self._stats.status = WatcherStatus.STOPPED
            raise

        except AuthError:
            logger.error(f"{self._logger_template} Ошибка авторизации")
            raise

        except Exception as e:
            self._stats.status = WatcherStatus.ERROR
            self._stats.error_counts += 1
            self._stats.last_error_time = datetime.now()
            logger.error(f"{self._logger_template} Ошибка при получении и обработке данных: {e}")
            raise

        finally:
            # Если цикл завершился (stop или исключение), то вотчер больше не работает.
            self._config.working = False
            if self._stats.status != WatcherStatus.ERROR:
                self._stats.status = WatcherStatus.STOPPED

    def _enable_run_task(self, run_task):
        self._run_task = asyncio.create_task(run_task())
        def sync_callback(task: asyncio.Task):
            asyncio.create_task(
                self._check_on_error_watcher(task)
            )
        self._run_task.add_done_callback(sync_callback)

    async def _disable_run_task(self):
        task = self._run_task
        if task is None:
            self._stats.status = WatcherStatus.STOPPED
            return

        if task.done():
            self._stats.status = WatcherStatus.STOPPED
            self._run_task = None
            return

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._stats.status = WatcherStatus.STOPPED
            self._run_task = None



    async def start_watching(self) -> None:
        if self._run_task is not None and not self._run_task.done():
            return
        logger.debug(self._logger_template + f"Начало работы вотчера для {self.username}")
        self._config.working = True
        self._stats.status = WatcherStatus.WORKING
        # self.connection.subscribe(self.on_change_status_connection)
        # self._run_task = asyncio.create_task(self.run())
        self._enable_run_task(self.run)


    async def stop_watching(self) -> None:
        self._config.working = False
        # self.connection.unsubscribe(self.on_change_status_connection)
        await self._disable_run_task()

    # def pause(self):
    #     logger.warning(self._logger_template + f"Пауза вотчера")
    #     # self._stats.status = WatcherStatus.ERROR
    #     self._config.working = False
    #     self._stats.status = WatcherStatus.PAUSED
    #     self._run_task.cancel()

    def pause(self):
        logger.warning(self._logger_template + "Пауза вотчера")
        self._config.working = False
        self._stats.status = WatcherStatus.PAUSED

        task = self._run_task
        if task is not None and not task.done():
            task.cancel()

    def resume(self):
        logger.debug(self._logger_template + "Возобновление работы вотчера")
        self._config.working = True
        self._stats.status = WatcherStatus.WORKING
        logger.debug(f"self._run_task is done {self._run_task.done()}")
        logger.debug(f"self._run_task is None {self._run_task}")
        if self._run_task is None or self._run_task.done():
            # self._run_task = asyncio.create_task(self.run())
            self._enable_run_task(self.run)


    @property
    def task(self) -> asyncio.Task | None:
        return self._run_task

    @property
    def stats(self) -> WatcherStats:
        return self._stats

    @property
    def config(self) -> WatcherConfig:
        return self._config

    @property
    def is_running(self) -> bool:
        task = self._run_task
        return bool(self._config.working and task is not None and not task.done())

    def subscribe(self,
                  callback: Union[
            Callable[[WatcherEvent], None],
            Callable[[WatcherEvent], Awaitable[None]]
                  ]
            ):
        self._notifier.subscribe(callback)

    def unsubscribe(
            self,
            callback: Union[
                Callable[[WatcherEvent], None],
                Callable[[WatcherEvent], Awaitable[None]],
            ],
    ):
        self._notifier.unsubscribe(callback)
