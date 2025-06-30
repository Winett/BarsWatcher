from aiogram import Bot
from aiogram.types import InputMediaDocument, BufferedInputFile

from loguru import logger
from aiohttp import ClientSession, ClientTimeout

from watchers.bars.barsWatcher import WatcherKM
from watchers.osep.osepWatcher import WatcherOsep
import asyncio
from aiohttp import ConnectionTimeoutError

from watchers.exceptions import LoginError, ServerError500
from database.db import async_session
from services.user import UserService

from settings import settings

from typing import Dict, Optional, TypeVar, Type, Any, List, ClassVar
from abc import ABC, abstractmethod
import time

T = TypeVar('T', bound='Notificator')


class Notificator(ABC):
    bot: Optional[Bot] = None
    _instances: Dict[str, Dict[int, 'Notificator']] = {}

    _timeout_errors: Dict[str, Dict[int, List[float]]] = {}  # {class_name: {chat_id: [timestamp1, timestamp2]}}
    _timeout_threshold = 4  # Максимальное количество таймаутов перед отключением
    _timeout_window = 300  # Окно времени для подсчета таймаутов (5 минут)
    _reconnect_delay = 60  # Задержка между проверками восстановления соединения
    _suspended_instances: Dict[str, list[tuple[int, str, str]]] = {}  # {class_name: [(chat_id, username, password)]}

    timeout_after_error = 60

    _connection_check_url: ClassVar[str]

    def __init__(self, chat_id: int, username: str, password: str):
        self.chat_id = chat_id
        self.username = username
        self.password = password
        self.watcher = self._create_watcher()
        self._register_instance()

    def _register_instance(self):
        class_name = self.__class__.__name__
        if class_name not in self._instances:
            self._instances[class_name] = {}
        self._instances[class_name][self.chat_id] = self

    @abstractmethod
    def _create_watcher(self):
        pass

    async def notify(self, message: str, *, user_id: Optional[int] = None, **kwargs):
        if not self.bot:
            raise ValueError("Bot instance not set")
        target_id = user_id or self.chat_id
        try:
            await self.bot.send_message(target_id, message)
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {e}")

    async def start_watching(self) -> bool:
        try:
            if not await self.watcher.login():
                await self.notify(f"Ошибка авторизации в {self.__class__.__name__}")
                return False

            await self._after_login()
            asyncio.create_task(self._watch_loop())
            return True
        except LoginError as e:
            await self.notify(f"Ошибка авторизации \n Неверные данные для входа, обновите их")
            return False
        except Exception as e:
            await self.notify(f"Ошибка запуска мониторинга: {e.__class__.__name__} {e.args}", user_id=settings.admins[0])
            return False

    async def _after_login(self):
        pass

    async def _watch_loop(self):
        self.watcher.watching = True
        error_count = 0
        max_errors = 3
        error_window_seconds = 180
        last_error_time = None

        while self.watcher.watching:
            try:
                await self.watcher.watch(callback=self.notify)
                error_count = 0

            except (ServerError500, LoginError) as e:
                logger.error(f"Ошибка сервера: {e.__class__.__name__} {e.args} у {self.chat_id} {self.username}")
                current_time = time.time()

                if last_error_time and (current_time - last_error_time > error_window_seconds):
                    error_count = 0

                error_count += 1
                last_error_time = current_time

                if error_count >= max_errors:
                    await self.notify(f"Слишком много ошибок сервера подряд, отслеживание прекращено\n\nПопробуйте подключить отслеживание позже")
                    await self.notify(
                        f"Автоотключение из-за ошибок: {e.__class__.__name__} у {self.chat_id} {self.username}",
                        user_id=settings.admins[0])
                    self.stop_watching(self.chat_id)
                    async with async_session() as session:
                        if self.__class__.__name__ == "BarsNotificator":
                            await UserService(session).set_bars_status_used(self.chat_id, False)
                        else:
                            await UserService(session).set_osep_status_used(self.chat_id, False)
                    break
                await self.notify(f"Ошибка сервера: {e.__class__.__name__} {e.args} у {self.chat_id} {self.username}",
                                  user_id=settings.admins[0])

            except Exception as e:
                await self._handle_watch_error(e)
                error_count = 0

            await asyncio.sleep(self.timeout_after_error)

    async def _handle_watch_error(self, error: Exception):
        error_msg = f"{self.__class__.__name__} ошибка: {error.__class__.__name__} {self.chat_id} {self.username}"
        await self.notify(error_msg, user_id=settings.admin_id[0])
        logger.error(f"{error_msg}: {str(error)}")

    @classmethod
    def get_instance(cls: Type[T], chat_id: int) -> Optional[T]:
        return cls._instances.get(cls.__name__, {}).get(chat_id)

    @classmethod
    def stop_watching(cls, chat_id: int):
        if chat_id in cls._instances.get(cls.__name__, {}):
            instance = cls._instances[cls.__name__][chat_id]
            instance.watcher.stop()
            del cls._instances[cls.__name__][chat_id]

    @classmethod
    def stop_all(cls):
        for instance in list(cls._instances.get(cls.__name__, {}).values()):
            instance.watcher.stop()
        cls._instances[cls.__name__] = {}

    @classmethod
    def get_all_instances(cls) -> Dict[int, 'Notificator']:
        return cls._instances.get(cls.__name__, {}).copy()

    @classmethod
    def get_count_of_instances(cls):
        return len(cls._instances[cls.__class__.__name__])

    ### Обработка ошибки таймаута ###
    async def _handle_timeout_error(self):
        class_name = self.__class__.__name__
        current_time = time.time()

        if class_name not in self._timeout_errors:
            self._timeout_errors[class_name] = {}
        if self.chat_id not in self._timeout_errors[class_name]:
            self._timeout_errors[class_name][self.chat_id] = []

        self._timeout_errors[class_name][self.chat_id].append(current_time)

        recent_errors = [
            t for t in self._timeout_errors[class_name][self.chat_id]
            if current_time - t <= self._timeout_window
        ]
        self._timeout_errors[class_name][self.chat_id] = recent_errors

        if len(recent_errors) >= self._timeout_threshold:
            await self._handle_critical_timeout()

    async def _handle_critical_timeout(self):
        class_name = self.__class__.__name__

        self._suspended_instances[class_name] = [
            (inst.chat_id, inst.username, inst.password)
            for inst in self._instances.get(class_name, {}).values()
        ]

        msg = f"Критическое количество таймаутов соединения ({self._timeout_threshold} за {self._timeout_window} сек). Отключаю все вотчеры {class_name}."
        await self.notify(msg, user_id=settings.admins[0])
        logger.error(msg)

        self.stop_all()

        asyncio.create_task(self._monitor_connection_recovery())

    async def _monitor_connection_recovery(self):
        class_name = self.__class__.__name__
        logger.info(f"Начало мониторинга восстановления соединения для {class_name}")

        while True:
            try:
                if await self.test_connection():
                    logger.info(f"Соединение восстановлено для {class_name}")
                    await self._restart_all_instances()
                    break

            except Exception as e:
                logger.debug(f"Соединение еще не восстановлено: {e}")

            await asyncio.sleep(self._reconnect_delay)

    async def _restart_all_instances(self):
        class_name = self.__class__.__name__

        for chat_id, username, password in self._suspended_instances.get(class_name, []):
            try:
                new_instance = self.__class__(
                    chat_id=chat_id,
                    username=username,
                    password=password
                )
                new_instance.bot = self.bot

                if await new_instance.start_watching():
                    logger.info(f"Успешно перезапущен вотчер для {chat_id}")
                    await new_instance.notify("Мониторинг автоматически возобновлен после восстановления соединения")
                else:
                    logger.error(f"Не удалось перезапустить вотчер для {chat_id}")

            except Exception as e:
                logger.error(f"Ошибка при перезапуске вотчера {chat_id}: {e}")

    def _clear_timeout_errors(self):
        class_name = self.__class__.__name__
        if class_name in self._timeout_errors and self.chat_id in self._timeout_errors[class_name]:
            del self._timeout_errors[class_name][self.chat_id]

    @classmethod
    async def test_connection(cls):
        session = ClientSession(timeout=ClientTimeout(total=10))
        try:
            async with session.get(url=cls._connection_check_url, allow_redirects=False) as response:
                return response.status == 200
        except Exception as e:
            logger.error(f"Ошибка соединения: {e}")
            return False
        finally:
            await session.close()

    ### Конец логики обработки ошибки таймаута ###

class BarsNotificator(Notificator):
    _connection_check_url = "https://bars.mpei.ru/bars_web/"

    def __init__(self, chat_id: int, username: str, password: str):
        super().__init__(chat_id, username, password)


    def _create_watcher(self):
        return WatcherKM(self.username, self.password)

    async def _after_login(self):
        self.watcher.student_id = await self.watcher.get_student_id()

class OsepNotificator(Notificator):
    _connection_check_url = "https://mail.mpei.ru/CookieAuth.dll?GetLogon?curl=Z2F&reason=0&formdir=2"

    def __init__(self, chat_id: int, username: str, password: str):
        super().__init__(chat_id, username, password)

    def _create_watcher(self):
        return WatcherOsep(self.username, self.password)

    async def notify(self, message: str, *, user_id: Optional[int] = None, **kwargs):
        if not self.bot:
            raise ValueError("Bot instance not set")
        target_id = user_id or self.chat_id
        try:
            if kwargs.get('files'):
                files = kwargs['files']
                file_contents = []
                for file in files:
                    file_contents.append(await self.watcher.get_attachment(file.id))
                media_group = []
                for content, filename in zip(file_contents, files):
                    input_file = BufferedInputFile(content, filename=filename.name)

                    media_group.append(InputMediaDocument(media=input_file, filename=filename.name))

                a = None
                for i in range(0, len(message), 4096):
                    a = await self.bot.send_message(
                        chat_id=target_id,
                        text=message[i:i+4096],
                        reply_to_message_id=a.message_id if a else None
                    ) # если вдруг письмо большое, то оно будет разбито на несколько частей
                    await asyncio.sleep(.3)

                for i in range(0, len(files), 10):
                    await self.bot.send_media_group(
                        chat_id=target_id,
                        media=media_group[i:i+10],
                        reply_to_message_id=a.message_id
                    )

                return
            for i in range(0, len(message), 4096):
                await self.bot.send_message(
                    chat_id=target_id,
                    text=message[i:i+4096],
                )
                await asyncio.sleep(.3)

        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {e}")