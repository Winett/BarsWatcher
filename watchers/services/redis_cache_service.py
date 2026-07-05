import json
import time
from typing import Any, Optional

import redis.asyncio as aioredis
from loguru import logger
from pydantic import BaseModel

__all__ = ["RedisCacheService"]

KEY_PREFIX = "bars_watcher:"


class RedisCacheService:
    """Сервис кэша на базе Redis."""

    def __init__(self, redis_url: str):
        self._redis_url = redis_url
        self._redis: Optional[aioredis.Redis] = None

    async def connect(self):
        """Подключение к Redis."""
        self._redis = aioredis.from_url(
            self._redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
        await self._redis.ping()
        logger.info(f"Redis подключён: {self._redis_url}")

    async def close(self):
        """Закрытие соединения."""
        if self._redis:
            await self._redis.aclose()
            logger.info("Redis соединение закрыто")

    def _key(self, key: str) -> str:
        return f"{KEY_PREFIX}{key}"

    async def get(self, key: str) -> Optional[Any]:
        """Получить значение по ключу."""
        raw = await self._redis.get(self._key(key))
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"Не удалось десериализовать значение для ключа {key}")
            return None

    async def set(self, key: str, value: Any, ttl: Optional[float] = None):
        """Установить значение с опциональным TTL (в секундах)."""
        serialized = json.dumps(self._serialize(value), ensure_ascii=False)
        if ttl:
            await self._redis.setex(self._key(key), int(ttl), serialized)
        else:
            await self._redis.set(self._key(key), serialized)

    async def delete(self, key: str) -> bool:
        """Удалить ключ. Возвращает True если ключ существовал."""
        result = await self._redis.delete(self._key(key))
        return result > 0

    async def exists(self, key: str) -> bool:
        """Проверить существование ключа."""
        return bool(await self._redis.exists(self._key(key)))

    async def keys(self) -> list[str]:
        """Получить все ключи с префиксом bars_watcher:"""
        raw_keys = await self._redis.keys(f"{KEY_PREFIX}*")
        prefix_len = len(KEY_PREFIX)
        return [k[prefix_len:] for k in raw_keys]

    async def clear(self):
        """Удалить все ключи с префиксом bars_watcher:."""
        keys = await self._redis.keys(f"{KEY_PREFIX}*")
        if keys:
            await self._redis.delete(*keys)

    async def get_with_metadata(self, key: str) -> Optional[dict[str, Any]]:
        """Получить значение с метаданными (created_at, ttl, expires_at)."""
        raw = await self._redis.get(self._key(key))
        if raw is None:
            return None

        ttl = await self._redis.ttl(self._key(key))

        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None

        now = time.time()
        return {
            "value": value,
            "ttl": ttl if ttl > 0 else None,
            "expires_at": now + ttl if ttl > 0 else None,
        }

    @staticmethod
    def _serialize(value: Any) -> Any:
        """Рекурсивная сериализация (Pydantic → dict)."""
        if isinstance(value, BaseModel):
            return value.model_dump()
        elif isinstance(value, dict):
            return {k: RedisCacheService._serialize(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [RedisCacheService._serialize(v) for v in value]
        return value
