import asyncio
from typing import Any, Callable, Dict, Awaitable

from aiogram import BaseMiddleware
from aiogram.types import Message
from loguru import logger


class MediaGroupMiddleware(BaseMiddleware):
    def __init__(self):
        super().__init__()
        self._buffer: Dict[str, list[Message]] = {}

    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: Dict[str, Any],
    ) -> Any:
        if not event.media_group_id:
            return await handler(event, data)

        key = f"{event.chat.id}:{event.media_group_id}"
        self._buffer.setdefault(key, []).append(event)
        logger.debug(f"MediaGroup buffer: key={key}, count={len(self._buffer[key])}")

        await asyncio.sleep(0.5)

        messages = self._buffer.pop(key, [])
        logger.debug(f"MediaGroup collected: {len(messages)} messages for key={key}")

        if not messages:
            return None

        data["media_group_messages"] = messages
        return await handler(event, data)
