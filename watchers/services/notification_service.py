from abc import ABC

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputMediaDocument

from watchers.utils.rate_limiter import RateLimiter


import asyncio

from watchers.models.mail_models import AttachmentData


class BaseNotificationService(ABC):

    async def send_message(self, user_id: int, message: str) -> bool: ...

    async def send_message_with_documents(self, user_id: int, message: str, files: list[AttachmentData]) -> bool: ...



class TelegramNotificationService(BaseNotificationService):

    bot: Bot = None
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):

        if hasattr(self, 'initialized'):
            return
        self.rate_limiter = RateLimiter(max_requests=25, period_seconds=1)

        self.initialized = True


    @classmethod
    def set_bot_instance(cls, bot: Bot) -> None:
        cls.bot = bot

    @staticmethod
    def prepare_message(message: str) -> list[str]:
        messages = []

        for i in range(0, len(message), 4096):
            messages.append(message[i:i+4096])

        return messages

    @staticmethod
    def prepare_documents(files: list[AttachmentData]):
        documents = []

        docs = []
        for file in files:
            input_file = BufferedInputFile(file.content, filename=file.filename)
            docs.append(InputMediaDocument(media=input_file, filename=file.filename))
            if len(docs) == 10:
                documents.append(docs)
                docs = []
        if docs:
            documents.append(docs)
        return documents


    async def send_message(self, user_id: int, message: str) -> bool:
        a = None
        try:
            for message in self.prepare_message(message):
                while time_to_sleep := self.rate_limiter.can_request():
                    await asyncio.sleep(time_to_sleep)
                a = await self.bot.send_message(chat_id=user_id, text=message, reply_to_message_id=a.message_id if a else None)
                self.rate_limiter.record_request()
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            for message in self.prepare_message(message):
                while time_to_sleep := self.rate_limiter.can_request():
                    await asyncio.sleep(time_to_sleep)
                a = await self.bot.send_message(chat_id=user_id, text=message,
                                                reply_to_message_id=a.message_id if a else None)
                self.rate_limiter.record_request()
            return True
        except:
            return False

    async def send_message_with_documents(self, user_id: int, message: str, files: list[AttachmentData]) -> bool:
        try:

            await self.send_message(user_id=user_id, message=message)
            for docs in self.prepare_documents(files):
                while time_to_sleep := self.rate_limiter.can_request():
                    await asyncio.sleep(time_to_sleep)
                a = await self.bot.send_media_group(chat_id=user_id, media=docs)
                self.rate_limiter.record_request()
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            await self.send_message(user_id=user_id, message=message)
            for docs in self.prepare_documents(files):
                while time_to_sleep := self.rate_limiter.can_request():
                    await asyncio.sleep(time_to_sleep)
                a = await self.bot.send_media_group(chat_id=user_id, media=docs)
                self.rate_limiter.record_request()
            return True
        except:
            return False



