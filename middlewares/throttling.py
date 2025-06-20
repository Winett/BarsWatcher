from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from typing import Callable, Dict, Any, Awaitable
import time
from collections import defaultdict


class ThrottlingMiddleware(BaseMiddleware):
    rate_limit: float = 0.6

    def __init__(self, rate_limit: float = None):
        if rate_limit:
            self.rate_limit = rate_limit
        self.user_last_request = defaultdict(float)

    async def __call__(
            self,
            handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
            event: TelegramObject,
            data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        user_id = user.id
        current_time = time.time()

        if current_time - self.user_last_request[user_id] < self.rate_limit:
            return

        self.user_last_request[user_id] = current_time
        return await handler(event, data)