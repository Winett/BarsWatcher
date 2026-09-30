"""Снимки данных для diff — Redis с автоматическим TTL."""

from __future__ import annotations

import json

from arq.connections import ArqRedis

from src.core.config import settings

__all__ = ["SnapshotStore"]


class SnapshotStore:
    def __init__(self, redis: ArqRedis, ttl_seconds: int | None = None):
        self.redis = redis
        self.ttl = ttl_seconds or settings.snapshot_ttl_seconds

    @staticmethod
    def marks_key(user_id: int) -> str:
        return f"snapshot:marks:{user_id}"

    @staticmethod
    def absences_key(user_id: int) -> str:
        return f"snapshot:absences:{user_id}"

    @staticmethod
    def folders_key(user_id: int) -> str:
        return f"snapshot:folders:{user_id}"

    async def get(self, key: str) -> dict | None:
        raw = await self.redis.get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    async def set(self, key: str, data: dict) -> None:
        await self.redis.set(
            key, json.dumps(data, ensure_ascii=False), ex=self.ttl
        )
