from abc import ABC, abstractmethod
from asyncio import Queue, Event

from aiogram import Bot
import asyncio

from aiogram.types import BufferedInputFile, InputMediaDocument

from .model import NotificatorMessage
from loguru import logger

class BaseNotificator(ABC):

    @abstractmethod
    async def notify(self, message: NotificatorMessage):
        pass

class TelegramNotificator(BaseNotificator):
    _queue: Queue[NotificatorMessage] = Queue()
    _global_worker: asyncio.Task = None
    _bot: Bot = None

    def __init__(self, user_id: int):
        self.user_id = user_id

        if TelegramNotificator._bot is None:
            raise ValueError("Bot instance not set")

        if TelegramNotificator._global_worker is None:
            TelegramNotificator._global_worker = asyncio.create_task(self._worker())

    async def notify(self, message: NotificatorMessage, **kwargs):
        await self._queue.put(message)

    async def _process_message(self, message_el):
        message, files, user_id = message_el.message, message_el.files, message_el.user_id

        media_group = []
        for attachment in files:
            input_file = BufferedInputFile(attachment.content, filename=attachment.filename)
            media_group.append(InputMediaDocument(media=input_file, filename=attachment.filename))

        a = None
        for i in range(0, len(message), 4096):
            try:
                a = await self._bot.send_message(
                    chat_id=user_id,
                    text=message[i:i + 4096],
                    reply_to_message_id=a.message_id if a else None
                )
                await asyncio.sleep(.3)
            except Exception as e:
                logger.error(f"Ошибка отправки сообщения от {message_el.watcher.value} {user_id}")
                await self._bot.send_message(chat_id=user_id,
                                            text=f"Не удалось отправить сообщение о новом событии из {message_el.watcher.value}")
                break

        for i in range(0, len(files), 10):
            try:
                await self._bot.send_media_group(
                    chat_id=user_id,
                    media=media_group[i:i + 10],
                    reply_to_message_id=a.message_id
                )
                await asyncio.sleep(.3)
            except Exception as e:
                logger.error(f"Ошибка отправки файлов от {message_el.watcher.value} {user_id}")
                await self._bot.send_message(chat_id=user_id,
                                            text=f"Не удалось отправить файлы из нового события из {message_el.watcher.value}")
                break


    async def _worker(self):
        while True:
            message_el = await self._queue.get()
            await self._process_message(message_el)
            self._queue.task_done()
            await asyncio.sleep(.2)
