"""Настройки arq-воркера: очередь worker'а + фоновый планировщик."""

from __future__ import annotations

import asyncio

from arq.connections import RedisSettings

from src.core.config import settings
from src.events import QUEUE_WORKER, dispatch_event

# импорт ради регистрации обработчиков событий (декораторы @subscribe)
from src.workers import handlers as _handlers  # noqa: F401
from src.workers.reminders import reminder_loop
from src.workers.scheduler import scheduler_loop


async def on_startup(ctx: dict) -> None:
    ctx["scheduler_task"] = asyncio.create_task(scheduler_loop())
    ctx["reminder_task"] = asyncio.create_task(reminder_loop())


async def on_shutdown(ctx: dict) -> None:
    for name in ("scheduler_task", "reminder_task"):
        task = ctx.get(name)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


class WorkerSettings:
    functions = [dispatch_event]
    queue_name = QUEUE_WORKER

    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    on_startup = on_startup
    on_shutdown = on_shutdown

    max_jobs = 20
    job_timeout = 300
    keep_result = 0
    retry_jobs = True
    max_tries = 3
