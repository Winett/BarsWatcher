"""Напоминания о проставлении пар (для ответственных за журнал).

Как работает: раз в день в **18:00 GMT+3** (или bars_settings.reminder_time)
напоминание уходит ответственным за журнал с включённой настройкой «Пары» и
включённым отслеживанием БАРС. Отправка не дублируется: маркер в Redis
`reminder:YYYY-MM-DD:user_id` живёт 25 часов.

Тестирование (sim/file): `SIM_REMINDER_INTERVAL=N` — напоминание приходит
каждые N секунд всем, у кого включено отслеживание БАРС (требования
«ответственный» и «включены Пары» в тесте не применяются — иначе уведомление
невозможно проверить), чтобы не дожидаться 18:00.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, time

from loguru import logger
from sqlalchemy import select

from src.core.config import settings
from src.core.timefmt import MSK
from src.db.database import session_factory
from src.db.models import BarsProfile, BarsSettings, BarsState, User, WatchStates
from src.events import ReminderDue, get_redis, publish

log = logger.bind(component="reminders")

DEFAULT_REMINDER_TIME = time(18, 0)  # GMT+3
TICK_SECONDS = 30  # период проверки наступления времени

__all__ = ["reminder_tick", "reminder_loop"]


async def _targets(*, soft: bool) -> list[tuple[int, time | None]]:
    """(user_id, reminder_time).

    soft=True (тестовый режим): все с включённым отслеживанием БАРС.
    soft=False: ответственные за журнал с включённой настройкой «Пары».
    """
    async with session_factory() as session:
        stmt = (
            select(User.id, BarsSettings.reminder_time)
            .join(BarsSettings, BarsSettings.user_id == User.id, isouter=True)
            .join(BarsProfile, BarsProfile.user_id == User.id, isouter=True)
            .join(BarsState, BarsState.user_id == User.id, isouter=True)
            .where(
                User.is_active.is_(True),
                User.is_blocked.is_(False),
                BarsState.status == WatchStates.ACTIVE,
            )
        )
        if not soft:
            stmt = stmt.where(
                BarsSettings.reminders_enabled.is_(True),
                BarsProfile.is_journal_responsible.is_(True),
            )
        rows = await session.execute(stmt)
        seen: dict[int, time | None] = {}
        for row in rows:
            # дубли (если join дал несколько строк) не плодят повторные напоминания
            seen.setdefault(row[0], row[1])
        return list(seen.items())


async def reminder_tick() -> int:
    """Одна выборка «кому напомнить». Возвращает число напоминаний."""
    now = datetime.now(UTC).astimezone(MSK)
    today = now.strftime("%Y-%m-%d")
    redis = await get_redis()
    sent = 0

    test_interval = (
        settings.sim_reminder_interval_seconds
        if settings.integration_mode != "real"
        else 0
    )
    targets = await _targets(soft=test_interval > 0)
    log.debug(
        "напоминание: кандидатов {} | режим {}",
        len(targets),
        "тест" if test_interval else f"{DEFAULT_REMINDER_TIME.hour}:{DEFAULT_REMINDER_TIME.minute:02d} GMT+3",
    )

    for user_id, reminder_time in targets:
        if test_interval > 0:
            # тестовый режим: каждые N секунд, без привязки к 18:00
            key = f"reminder:test:{user_id}"
            if not await redis.set(key, "1", nx=True, ex=max(test_interval - 5, 5)):
                continue
        else:
            # обычный режим: своё время (по умолчанию 18:00 GMT+3), раз в день
            target = reminder_time or DEFAULT_REMINDER_TIME
            if now.time() < target:
                log.debug(
                    "напоминание: user={} ждёт {} (сейчас {})",
                    user_id,
                    target.strftime("%H:%M"),
                    now.strftime("%H:%M"),
                )
                continue
            key = f"reminder:{today}:{user_id}"
            if not await redis.set(key, "1", nx=True, ex=25 * 3600):
                log.debug(
                    "напоминание: user={} пропущено (сегодня уже отправлено)",
                    user_id,
                )
                continue
        issues = await _collect_issues(user_id)
        if issues is not None and not issues:
            # все пары в порядке (или сегодня их нет) — напоминать не о чем
            log.info("напоминание: user={} — все пары в порядке, пропуск", user_id)
            continue
        await publish(ReminderDue(user_id=user_id, issues=issues or []))
        log.info("напоминание отправлено | user={}", user_id)
        sent += 1
    return sent


async def _collect_issues(user_id: int) -> list[dict] | None:
    """Проблемы пар «журнал vs расписание» за сегодня.

    Детали для напоминания: пара не проставлена, статус «требуется
    корректировка», «на рассмотрении» без отметок студентов.
    None — собрать не удалось (тогда напоминание шлём без деталей).
    """
    from src.services.lessons import find_lesson_issues
    from src.workers.handlers import authed_bars_client

    try:
        today = datetime.now(MSK).date()
        # сессия восстанавливается из БД — «уже авторизован» работает и тут
        client, ok = await authed_bars_client(user_id)
        if not ok:
            return None
        lessons = await client.fetch_today_lessons(today)
        schedule = await client.fetch_schedule(today, today)
        return [issue.to_dict() for issue in find_lesson_issues(schedule, lessons)]
    except Exception as error:
        log.warning(
            "напоминание: не удалось собрать проблемы пар | user={}: {}",
            user_id,
            error,
        )
        return None


async def reminder_loop() -> None:
    log.info(
        "напоминания запущены | 18:00 GMT+3 (тест: SIM_REMINDER_INTERVAL={})",
        settings.sim_reminder_interval_seconds,
    )
    while True:
        try:
            count = await reminder_tick()
            if count:
                log.info("напоминаний отправлено: {}", count)
        except asyncio.CancelledError:
            log.info("напоминания остановлены")
            raise
        except Exception as error:
            log.error("ошибка тика напоминаний: {}", error)
        await asyncio.sleep(TICK_SECONDS)
