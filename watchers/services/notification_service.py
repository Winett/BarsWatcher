from abc import ABC
from html import escape as html_escape

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import BufferedInputFile, FSInputFile, InputMediaDocument
from loguru import logger

from watchers.utils.rate_limiter import RateLimiter


import asyncio

from pathlib import Path
from dataclasses import dataclass

from watchers.models.mail_models import AttachmentData


@dataclass
class DiskFile:
    path: Path
    original_name: str


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
        """Разбить сообщение на части по 4096 символов (лимит Telegram)."""
        messages = []

        for i in range(0, len(message), 4096):
            messages.append(message[i:i+4096])

        return messages

    @staticmethod
    def escape_html(text: str) -> str:
        """Экранировать HTML-сущности для безопасной отправки в Telegram (parse_mode=HTML)."""
        return html_escape(text)

    @staticmethod
    def prepare_documents(files: list):
        documents = []

        docs = []
        for file in files:
            if isinstance(file, DiskFile):
                input_file = FSInputFile(file.path, filename=file.original_name)
                docs.append(InputMediaDocument(media=input_file, filename=file.original_name))
            elif isinstance(file, Path):
                input_file = FSInputFile(file, filename=file.name)
                docs.append(InputMediaDocument(media=input_file, filename=file.name))
            else:
                input_file = BufferedInputFile(file.content, filename=file.filename)
                docs.append(InputMediaDocument(media=input_file, filename=file.filename))
            if len(docs) == 10:
                documents.append(docs)
                docs = []
        if docs:
            documents.append(docs)
        return documents

    async def send_message(self, user_id: int, message: str, _retries: int = 0, _max_retries: int = 5) -> bool:
        try:
            message_parts = list(self.prepare_message(message))
            last_message = None

            for part in message_parts:
                async with self.rate_limiter:
                    last_message = await self.bot.send_message(
                        chat_id=user_id,
                        text=part,
                        reply_to_message_id=last_message.message_id if last_message else None
                    )
            logger.info(f"Сообщение отправлено пользователю {user_id}")
            return True

        except TelegramRetryAfter as e:
            if _retries >= _max_retries:
                logger.error(f"Превышен лимит retry для {user_id} ({_max_retries} попыток)")
                return False
            await asyncio.sleep(e.retry_after)
            return await self.send_message(user_id, message, _retries + 1, _max_retries)

        except Exception as e:
            logger.warning(f"Ошибка отправки сообщения пользователю {user_id}: {e}")
            return False

    async def send_message_with_documents(self, user_id: int, message: str, files: list[AttachmentData], _retries: int = 0, _max_retries: int = 5) -> bool:
        try:
            if message:
                await self.send_message(user_id, message)

            documents_groups = list(self.prepare_documents(files))

            for docs_group in documents_groups:
                async with self.rate_limiter:
                    await self.bot.send_media_group(
                        chat_id=user_id,
                        media=docs_group
                    )
            logger.info(f"Документы отправлены пользователю {user_id} ({len(files)} файлов)")
            return True

        except TelegramRetryAfter as e:
            if _retries >= _max_retries:
                logger.error(f"Превышен лимит retry для {user_id} ({_max_retries} попыток)")
                return False
            await asyncio.sleep(e.retry_after)
            return await self.send_message_with_documents(user_id, message, files, _retries + 1, _max_retries)

        except Exception as e:
            logger.warning(f"Ошибка отправки документов пользователю {user_id}: {e}")
            return False