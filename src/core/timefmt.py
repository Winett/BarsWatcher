"""Время: хранение в UTC, отображение для пользователей — GMT+3 (Москва).

Требование: всё, что видит пользователь (панели, уведомления), показывается
в GMT+3; напоминание о проставлении пар — тоже по GMT+3 (18:00).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta, timezone

__all__ = ["MSK", "now_msk", "to_msk", "fmt_dt", "fmt_time", "fmt_clock"]

# фиксированный GMT+3 — без перехода на летнее время
MSK = timezone(timedelta(hours=3), name="GMT+3")


def now_msk() -> datetime:
    return datetime.now(UTC).astimezone(MSK)


def to_msk(value: datetime | None) -> datetime | None:
    """Привести момент к GMT+3 (naive считается UTC)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(MSK)


def fmt_dt(value: datetime | None) -> str:
    """«ДД.ММ.ГГГГ ЧЧ:ММ» в GMT+3 (или «НЕТ»)."""
    msk = to_msk(value)
    return "НЕТ" if msk is None else msk.strftime("%d.%m.%Y %H:%M")


def fmt_time(value: datetime | None) -> str:
    """«ЧЧ:ММ ДД.ММ.ГГГГ» в GMT+3 (или «НЕТ»)."""
    msk = to_msk(value)
    return "НЕТ" if msk is None else msk.strftime("%H:%M %d.%m.%Y")


def fmt_clock(value: time | None) -> str:
    """«ЧЧ:ММ» без даты."""
    return "НЕТ" if value is None else value.strftime("%H:%M")
