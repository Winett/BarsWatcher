from aiogram import Bot
from aiogram.types import InputFile, InputMediaDocument, BufferedInputFile
from io import BytesIO

from loguru import logger

from watchers.bars.barsWatcher import WatcherKM
from watchers.osep.osepWatcher import WatcherOsep
from watchers.osep.osepmodel import AttachmentData
import asyncio

from watchers.exceptions import LoginError

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
                files = kwargs['files']  # list[AttachmentData]

                # Параллельная загрузка файлов
                file_contents = await asyncio.gather(
                    *[self.watcher.get_attachment(file.id) for file in files]
                )
                media_group = []
                for content, filename in zip(file_contents, files):
                    input_file = BufferedInputFile(content, filename=filename.name)

                    media_group.append(InputMediaDocument(media=input_file, filename=filename.name))


                await self.bot.send_message(
                    chat_id=target_id,
                    text=message
                )
                for i in range(0, len(files), 10):
                    await self.bot.send_media_group(
                        chat_id=target_id,
                        media=media_group[i:i+10]
                    )

                return
            await self.bot.send_message(target_id, message)

        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {e}")



# class BarsNotificator(Notificator):
#     _instances: dict[int, 'BarsNotificator'] = {}  # Хранит экземпляры по chat_id
#
#     def __init__(self, chat_id: int, username: str, password: str):
#         self.chat_id = chat_id
#         self.watcher = WatcherKM(username, password)
#         self._instances[chat_id] = self  # Регистрируем экземпляр
#
#     async def notify(self, message: str, *, user_id: Optional[int] = None):
#         if user_id:
#             await self.bot.send_message(user_id, message)
#             return
#         await self.bot.send_message(self.chat_id, message)
#
#     async def start_watching(self) -> bool:
#         """Запускает мониторинг изменений"""
#         # logger.debug(f"Попытка авторизации в БАРС...")
#         try:
#             if not await self.watcher.login():
#                 await self.notify("Ошибка авторизации в БАРС\n\nПроверьте логин и пароль и обновите их")
#                 return False
#         except LoginError as e:
#             await self.notify(f"Ошибка авторизации в БАРС: {str(e)}\n\nПроверьте логин и пароль и обновите их")
#             return False
#         # logger.debug(f"Попытка получить student_id...")
#         self.watcher.student_id = await self.watcher.get_student_id()
#         # logger.debug(f"Попытка получить student_id... Успешно: {self.watcher.student_id is not None}")
#         asyncio.create_task(self._watch_loop())
#         # logger.debug(f"##Мониторинг запущен##")
#         return True
#
#     async def _watch_loop(self):
#         """Внутренний цикл мониторинга"""
#         self.watcher.watching = True
#         while self.watcher.watching:
#             try:
#                 await self.watcher.watch(callback=self.notify)
#             except Exception as e:
#                 await self.notify(f"Ошибка мониторинга: {e.__class__.__name__} {e.args} {str(e)}", user_id=settings.admin_id[0])
#
#                 # await self.notify(f"Ошибка мониторинга: {str(e)}")
#                 await asyncio.sleep(5)
#
#     @classmethod
#     def get_instance(cls, chat_id: int) -> Optional['BarsNotificator']:
#         """Возвращает экземпляр по chat_id"""
#         return cls._instances.get(chat_id)
#
#     @classmethod
#     def stop_watching(cls, chat_id: int):
#         """Останавливает мониторинг для пользователя"""
#         if instance := cls._instances.get(chat_id):
#             instance.watcher.stop()
#             del cls._instances[chat_id]

