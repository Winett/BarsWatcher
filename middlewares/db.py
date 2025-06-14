from aiogram import BaseMiddleware
from aiogram.types import Message, CallbackQuery

from database.db import async_session

class DatabaseMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        async with async_session() as session:
            data["session"] = session
            return await handler(event, data)