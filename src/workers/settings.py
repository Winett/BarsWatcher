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

# фоновые задачи (планировщик/напоминания/лимит) живут НЕ в ctx, а здесь:
# при аварийной остановке worker'а on_shutdown может не выполниться (close()
# падает на недоступном Redis ещё ДО on_shutdown) — тогда старые задачи
# остаются на event loop, и рестарт worker.py запускает ВТОРОЙ планировщик.
# Второй планировщик = повторные задачи опроса. Регистр позволяет on_startup
# отменить остатки предыдущего запуска перед стартом новых.
_bg_tasks: list = []


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


async def _cancel_bg_tasks() -> None:
    """Отменить фоновые задачи прошлого запуска (если они ещё живы)."""
    tasks = list(_bg_tasks)
    _bg_tasks.clear()
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as error:
            log.debug("фоновая задача завершилась с ошибкой при остановке: {}", error)


async def on_startup(ctx: dict) -> None:
    # защита от двойного планировщика: см. комментарий к _bg_tasks
    await _cancel_bg_tasks()
    _bg_tasks.extend(
        [
            asyncio.create_task(scheduler_loop()),
            asyncio.create_task(reminder_loop()),
        ]
    )
    # лимит применяется ДО начала обработки задач — без окна «лишней» параллельности
    await _apply_max_jobs()
    _bg_tasks.append(asyncio.create_task(max_jobs_sync_loop()))


async def on_shutdown(ctx: dict) -> None:
    await _cancel_bg_tasks()


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
