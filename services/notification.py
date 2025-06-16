import asyncio
from datetime import datetime

from sqlalchemy.ext.asyncio import  AsyncSession
from sqlalchemy import select
from aiogram import Bot
from aiogram.types import InputMediaDocument, BufferedInputFile
from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError, TelegramNotFound
from loguru import logger

from services.user import UserService
from database.models import User

from settings import settings


class NotificationService:

    bot: Bot = None

    def __init__(self, session_maker: AsyncSession) -> None:
        self.session_maker = session_maker

    async def notify_all_users(self, message: str) -> None:
        msg_errors = []
        logger.info("Начинаю рассылку сообщений")
        user_service = UserService(self.session_maker)
        users = await user_service.find_all_users()
        logger.info(f"Получил всех пользователей: {len(users)}")
        for user in users:
            try:
                await self.bot.send_message(user.user_id, message)
                await asyncio.sleep(.2)
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except TelegramForbiddenError as e:
                msg_errors.append(f"Пользователь с id {user.user_id} заблокировал бота")
            except TelegramNotFound:
                msg_errors.append(f"Пользователь с id {user.user_id} удалил аккаунт из телеграм")
        msg = ("Рассылка сообщений окончена!\n"
               f"Было отправлено сообщений: {len(users) - len(msg_errors)}/{len(users)}\n\n"
               )
        if msg_errors:
            msg += f"Произошли ошибки при отправке сообщений:\n{'\n'.join(msg_errors)}"
            await self.notify_admins(message=msg, documents=[InputMediaDocument(media=BufferedInputFile(msg.encode('utf-8'), f"Отчёт о рассылке за {datetime.now().strftime('%H:%M %d.%m.%Y')}.txt"))])
            return
        logger.disable("services.notification")
        await self.notify_admins(message=msg)
        logger.enable("services.notification")
        logger.info("Рассылка сообщений окончена")


    async def notify_user(self, user_id: int, message: str, *, documents: list[InputMediaDocument]=None) -> None:
        try:
            logger.info(f"Отсылаю сообщение пользователю {user_id}")
            if documents:
                msg = await self.bot.send_message(user_id, message)
                for i in range(0, len(documents), 10):
                    await self.bot.send_media_group(user_id, documents[i:i+10], reply_to_message_id=msg.message_id)
                    await asyncio.sleep(.2)
            else:
                await self.bot.send_message(user_id, message)
            logger.info(f"Сообщение пользователю {user_id} отправлено")
        except (TelegramForbiddenError, TelegramNotFound) as e:
            logger.error(f"Произошла ошибка при отправке сообщения пользователю {user_id}: {e}")
            await self.notify_admins(message=f"Произошла ошибка при отправке сообщения пользователю {user_id}: {e}")

    async def notify_admins(self, message: str, *, documents: list[InputMediaDocument]=None) -> None:

        for admin in settings.admins:
            await self.notify_user(user_id=admin, message=message, documents=documents)
            await asyncio.sleep(.2)


