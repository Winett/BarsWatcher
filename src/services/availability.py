"""Отслеживание доступности сервисов (circuit breaker + журнал up/down).

Состояние хранится в Redis: availability:{service} = {"up": bool, "since": iso,
"streak": {"ok": bool, "since": iso}}.

Правило статуса — по времени, а не по числу попыток (временные сбои не
считаются падением): сервис считается упавшим, если **в течение 3 минут**
не было ни одного успешного ответа; поднятым — если **в течение 3 минут**
нет ошибок. Любой успех внутри серии ошибок сбрасывает окно (и наоборот).
Проверки при этом продолжаются всегда — только так можно заметить
восстановление.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from arq.connections import ArqRedis

__all__ = ["StatusTransition", "AvailabilityTracker", "STREAK_WINDOW"]

STREAK_WINDOW = timedelta(minutes=3)  # окно «3 минуты без улучшений»


def _streak_since(streak: dict, fallback: datetime) -> datetime:
    """Время начала серии; старый формат ({'ok','count'}) трактуем как новую серию."""
    raw = streak.get("since")
    if not raw:
        return fallback
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return fallback


@dataclass
class StatusTransition:
    changed: bool
    is_up: bool
    since: datetime


class AvailabilityTracker:
    def __init__(self, redis: ArqRedis):
        self.redis = redis

    @staticmethod
    def _key(service: str) -> str:
        return f"availability:{service}"

    async def _load(self, service: str) -> dict | None:
        raw = await self.redis.get(self._key(service))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    async def get_status(self, service: str) -> tuple[bool | None, datetime | None]:
        data = await self._load(service)
        if data is None:
            return None, None
        return data["up"], datetime.fromisoformat(data["since"])

    async def report(
        self, service: str, ok: bool, now: datetime | None = None
    ) -> StatusTransition:
        now = now or datetime.now(UTC)
        data = await self._load(service)

        if data is None:
            # первый отчёт — открываем начальный интервал сразу
            await self._store(service, ok, now, streak_ok=ok, streak_since=now)
            return StatusTransition(changed=True, is_up=ok, since=now)

        current_up: bool = data["up"]
        current_since = datetime.fromisoformat(data["since"])
        streak = data.get("streak") or {"ok": current_up, "since": now.isoformat()}

        if ok == current_up:
            # статус подтверждён — серия противоположных результатов обнуляется
            if streak.get("ok") != ok:
                streak = {"ok": ok, "since": now.isoformat()}
            await self._store(
                service,
                current_up,
                current_since,
                streak_ok=bool(streak.get("ok", ok)),
                streak_since=_streak_since(streak, now),
            )
            return StatusTransition(changed=False, is_up=current_up, since=current_since)

        # результат противоположен статусу: копим серию
        if streak.get("ok") != ok:
            streak = {"ok": ok, "since": now.isoformat()}
        streak_since = _streak_since(streak, now)
        if now - streak_since < STREAK_WINDOW:
            # ещё идёт окно «3 минуты» — временные сбои не дёргают статус
            await self._store(
                service,
                current_up,
                current_since,
                streak_ok=ok,
                streak_since=streak_since,
            )
            return StatusTransition(changed=False, is_up=current_up, since=current_since)

        # 3 минуты без улучшений — статус меняется
        await self._store(service, ok, now, streak_ok=ok, streak_since=now)
        return StatusTransition(changed=True, is_up=ok, since=now)

    async def _store(
        self,
        service: str,
        up: bool,
        since: datetime,
        *,
        streak_ok: bool,
        streak_since: datetime,
    ) -> None:
        await self.redis.set(
            self._key(service),
            json.dumps(
                {
                    "up": up,
                    "since": since.isoformat(),
                    "streak": {"ok": streak_ok, "since": streak_since.isoformat()},
                }
            ),
        )
