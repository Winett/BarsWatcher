"""Планировщик: единственный таймер в системе.

Раз в settings.scheduler_tick_seconds выбирает состояния, у которых пора
проверять, и роняет событие PollDue в очередь worker'а. Следующая проверка
сдвигается сразу при постановке (защита от дублей), обработчик уточняет
next_check после фактической попытки.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from loguru import logger

from src.core.config import settings
from src.db.database import session_factory
from src.db.models import ServiceKind
from src.events import PollDue, publish
from src.repositories.uow import UnitOfWork
from src.services import resolve_poll_settings

log = logger.bind(component="scheduler")

__all__ = ["scheduler_tick", "scheduler_loop"]


async def scheduler_tick() -> int:
    """Одна выборка «кого проверять». Возвращает число поставленных проверок.

    Проверки НЕ шлются разом — равномерно распределяются по времени
    (не более 30 запросов/сек), чтобы не заблокировать сервис при старте.
    """
    now = datetime.now(UTC)
    jobs: list[tuple[int, ServiceKind]] = []

    async with session_factory() as session:
        uow = UnitOfWork(session)

        bars_states = await uow.bars_state.get_bars_states_for_check(now)
        osep_states = await uow.osep_state.get_osep_states_for_check(now)

        # тестовые режимы (sim/file): короткий интервал, чтобы правки файлов
        # ответов быстро превращались в события (для real не используется)
        test_interval = (
            settings.sim_poll_interval_seconds
            if settings.integration_mode != "real"
            else None
        )

        for state in bars_states:
            poll = await resolve_poll_settings(session, state.user_id)
            interval = (
                test_interval
                or state.current_interval_seconds
                or poll.poll_interval_seconds
            )
            state.next_check = now + timedelta(seconds=interval)
            jobs.append((state.user_id, ServiceKind.BARS))

        for state in osep_states:
            poll = await resolve_poll_settings(session, state.user_id)
            interval = (
                test_interval
                or state.current_interval_seconds
                or poll.poll_interval_seconds
            )
            state.next_check = now + timedelta(seconds=interval)
            jobs.append((state.user_id, ServiceKind.OSEP))

        await uow.commit()

    # равномерно распределяем по времени: не более 30 запросов/сек
    # (1/30 сек ≈ 33ms между publish), чтобы не ударить по сервису разом
    for user_id, service in jobs:
        await publish(PollDue(user_id=user_id, service=service))
        await asyncio.sleep(1.0 / 30.0)

    return len(jobs)


async def scheduler_loop() -> None:
    log.info("планировщик запущен | тик {}с", settings.scheduler_tick_seconds)
    while True:
        try:
            count = await scheduler_tick()
            if count:
                log.debug("тик: поставлено проверок — {}", count)
        except asyncio.CancelledError:
            log.info("планировщик остановлен")
            raise
        except Exception as error:
            log.error("ошибка тика планировщика: {}", error)
        await asyncio.sleep(settings.scheduler_tick_seconds)
