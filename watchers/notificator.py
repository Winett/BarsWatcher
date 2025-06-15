from aiogram import Bot
from aiogram.types import InputMediaDocument, BufferedInputFile

from loguru import logger

from watchers.bars.barsWatcher import WatcherKM
from watchers.osep.osepWatcher import WatcherOsep
import asyncio

from watchers.exceptions import LoginError, ServerError500

from settings import settings

from typing import Dict, Optional, TypeVar, Type, Any
from abc import ABC, abstractmethod

T = TypeVar('T', bound='Notificator')


class Notificator(ABC):
    bot: Optional[Bot] = None
    _instances: Dict[str, Dict[int, 'Notificator']] = {}

    timeout_after_error = 60

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
        while self.watcher.watching:
            try:
                await self.watcher.watch(callback=self.notify)
            except ServerError500 as e:
                await self.notify(f"500 сервера, попробуйте повторить действие позже")
                await self.notify(f"Ошибка сервера: {e.__class__.__name__} {e.args} у {self.chat_id} {self.username}",
                                  user_id=settings.admins[0])
            except Exception as e:
                await self._handle_watch_error(e)
            await asyncio.sleep(self.timeout_after_error)

    async def _handle_watch_error(self, error: Exception):
        error_msg = f"{self.__class__.__name__} ошибка: {error.__class__.__name__}"
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

class BarsNotificator(Notificator):

    def __init__(self, chat_id: int, username: str, password: str):
        super().__init__(chat_id, username, password)


    def _create_watcher(self):
        return WatcherKM(self.username, self.password)

    async def _after_login(self):
        self.watcher.student_id = await self.watcher.get_student_id()

class OsepNotificator(Notificator):

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


                a = await self.bot.send_message(
                    chat_id=target_id,
                    text=message
                )
                #TODO: Сделать "ответ" на сообщение с письмом файлам
                for i in range(0, len(files), 10):
                    await self.bot.send_media_group(
                        chat_id=target_id,
                        media=media_group[i:i+10]
                    )

                return
            await self.bot.send_message(target_id, message)

        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {e}")