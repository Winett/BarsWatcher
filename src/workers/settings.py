"""Настройки arq-воркера: очередь worker'а + фоновый планировщик.

Параллельность проверок: жёсткий потолок MAX_JOBS_CEILING (его нельзя
превысить), а рабочий лимит — динамический и берётся из глобальной настройки
«Параллельных проверок» в админке (на лету, без перезапуска) либо из
WORKER_MAX_JOBS в .env, если админка не задала своего.
"""

from __future__ import annotations

import asyncio

from arq.connections import RedisSettings
from loguru import logger

from src.core.config import settings
from src.db.database import session_factory
from src.events import QUEUE_WORKER, dispatch_event
from src.repositories.uow import UnitOfWork

# импорт ради регистрации обработчиков событий (декораторы @subscribe)
from src.workers import handlers as _handlers  # noqa: F401
from src.workers.reminders import reminder_loop
from src.workers.scheduler import scheduler_loop

log = logger.bind(component="worker")

MAX_JOBS_CEILING = 20  # потолок; больше на сервере с одним ядром и не нужно
_SYNC_INTERVAL = 15  # как часто перечитывать лимит из БД

# ссылка на Worker (её передаёт worker.py после создания) — лимит меняется
# присваиванием worker.max_jobs: arq проверяет его на каждой итерации цикла
_worker_ref: list = []


def set_worker(worker) -> None:
    _worker_ref.clear()
    _worker_ref.append(worker)


async def _resolve_limit() -> int:
    """Рабочий лимит: админка (БД) → иначе WORKER_MAX_JOBS из .env."""
    limit = settings.worker_max_jobs
    try:
        async with session_factory() as session:
            uow = UnitOfWork(session)
            row = await uow.config.get_global()
        if row is not None and row.worker_max_jobs:
            limit = row.worker_max_jobs
    except Exception as error:
        # БД недоступна — работаем с лимитом из .env, не падаем
        log.debug("лимит воркеров: БД недоступна, берём .env: {}", error)
    return max(1, min(limit, MAX_JOBS_CEILING))


async def _apply_max_jobs() -> None:
    if not _worker_ref:
        return
    worker = _worker_ref[0]
    limit = await _resolve_limit()
    if worker.max_jobs != limit:
        log.info("лимит параллельных проверок изменён: {} → {}", worker.max_jobs, limit)
        worker.max_jobs = limit


async def max_jobs_sync_loop() -> None:
    """Периодически подхватывает лимит из админки (на лету)."""
    while True:
        try:
            await _apply_max_jobs()
        except Exception as error:
            log.warning("синхронизация лимита воркеров не удалась: {}", error)
        await asyncio.sleep(_SYNC_INTERVAL)


async def on_startup(ctx: dict) -> None:
    ctx["scheduler_task"] = asyncio.create_task(scheduler_loop())
    ctx["reminder_task"] = asyncio.create_task(reminder_loop())
    # лимит применяется ДО начала обработки задач — без окна «лишней» параллельности
    await _apply_max_jobs()
    ctx["max_jobs_task"] = asyncio.create_task(max_jobs_sync_loop())


async def on_shutdown(ctx: dict) -> None:
    for name in ("scheduler_task", "reminder_task", "max_jobs_task"):
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

    # ПОТОЛОК (семафор arq создаётся от него); рабочий лимит — динамический
    max_jobs = MAX_JOBS_CEILING
    job_timeout = 300
    keep_result = 0
    retry_jobs = True
    max_tries = 3
