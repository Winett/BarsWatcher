from aiogram import BaseMiddleware

from loguru import logger
from services.user import UserService
from database.db import async_session


class LoggingMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        if event.message is not None:
            message = f'[Message:  {event.message.text}]'
        else:
            message = f'[CallbackQuery: {event.callback_query.data}]'
        if (data.get('raw_state') is not None) and data.get('raw_state') in ['BarsState:bars_password', 'OsepState:osep_password']:
            message = f"[Watcher: Ввели пароль]"
        logger.debug(f"[user_id = {data['event_from_user'].id}] - [username = {data['event_from_user'].username}] - {message}")
        async with async_session() as session:
            try:
                await UserService(session).update_username(data['event_from_user'].id, data['event_from_user'].username)
            except AttributeError:
                pass
        return await handler(event, data)