"""Шина событий поверх arq (Redis).

publish(event)  → задача `dispatch_event` в очередь события (worker/bot).
@subscribe(X)   → регистрация обработчика события X в текущем процессе.
dispatch_event  → job-функция: дедупликация по event_id + вызов обработчиков.

Доставка at-least-once: обработчики должны быть идемпотентны; маркер
`events:seen:{event_id}` ставится после успешного выполнения, при ошибке
снимается — тогда arq повторит задачу.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from loguru import logger

from src.core.config import settings

from .contracts import EVENT_TYPES, Event

__all__ = [
    "subscribe",
    "publish",
    "dispatch_event",
    "dispatch_local",
    "get_redis",
    "get_pool",
]

Handler = Callable[[Event], Awaitable[None]]

_handlers: dict[str, list[Handler]] = {}
_pool: ArqRedis | None = None

SEEN_TTL_SECONDS = 24 * 3600


def subscribe(event_cls: type[Event]) -> Callable[[Handler], Handler]:
    """Декоратор: регистрирует обработчик события."""

    def decorator(fn: Handler) -> Handler:
        _handlers.setdefault(event_cls.event_type, []).append(fn)
        logger.debug(
            "events | зарегистрирован обработчик {} на {}",
            fn.__name__,
            event_cls.event_type,
        )
        return fn

    return decorator


async def get_pool() -> ArqRedis:
    global _pool
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def get_redis() -> ArqRedis:
    """Redis-клиент (для снимков/кэша) — тот же пул, что и у шины."""
    return await get_pool()


async def publish(event: Event) -> None:
    pool = await get_pool()
    await pool.enqueue_job(
        "dispatch_event", event.to_payload(), _queue_name=event.target_queue
    )
    logger.debug(
        "events | опубликовано {} → очередь {} (event_id={})",
        event.event_type,
        event.target_queue,
        event.event_id,
    )


async def dispatch_event(ctx: dict, payload: dict) -> None:
    """Job-функция arq: десериализация, дедупликация, вызов обработчиков."""
    event_type = payload.get("event_type")
    event_cls = EVENT_TYPES.get(event_type)
    if event_cls is None:
        logger.warning("events | неизвестный тип события: {}", event_type)
        return

    event = event_cls.from_payload(payload)
    redis = ctx["redis"]
    seen_key = f"events:seen:{event.event_id}"

    if not await redis.set(seen_key, "1", nx=True, ex=SEEN_TTL_SECONDS):
        logger.debug("events | дубликат {}, пропуск", event.event_id)
        return

    handlers = _handlers.get(event_type, [])
    if not handlers:
        logger.debug("events | у {} нет обработчиков в этом процессе", event_type)
        return

    for handler in handlers:
        try:
            await handler(event)
        except Exception as error:
            await redis.delete(seen_key)  # чтобы arq мог повторить задачу
            # без traceback — только суть ошибки (traceback'и лишь засоряют лог)
            logger.error(
                "events | обработчик {} упал на {}: {}",
                handler.__name__,
                event_type,
                error,
            )
            raise

    logger.debug(
        "events | {} обработано ({} обработчиков)", event_type, len(handlers)
    )


# ─────────────── локальный диспатч (симуляция/тесты без Redis) ───────────────

_seen_local: set[str] = set()


async def dispatch_local(event: Event) -> bool:
    """In-process диспатч события: дедуп по event_id + вызов обработчиков.

    Для симуляции и локальной проверки цепочки «событие → уведомление» без
    Redis/arq. В продакшене события идут через publish/dispatch_event.
    """
    if event.event_id in _seen_local:
        logger.debug("events | дубликат (local) {}, пропуск", event.event_id)
        return False
    _seen_local.add(event.event_id)

    handlers = _handlers.get(event.event_type, [])
    for handler in handlers:
        await handler(event)
    logger.debug(
        "events | (local) {} обработано ({} обработчиков)",
        event.event_type,
        len(handlers),
    )
    return True
