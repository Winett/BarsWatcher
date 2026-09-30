"""Тексты панелей бота (по схеме BarsWatcherBotLogic.drawio).

Время показывается в GMT+3 (см. src.core.timefmt).
"""

from __future__ import annotations

import os
from datetime import datetime, time

from src.core.timefmt import fmt_clock, fmt_dt, fmt_time
from src.db.models import WatchStates

__all__ = [
    "WELCOME",
    "service_panel_text",
    "bars_settings_text",
    "osep_settings_text",
    "services_text",
    "statistic_text",
    "fmt_datetime",
]

WELCOME = (
    "Добро пожаловать в бот по отслеживанию БАРС и ОСЭП\n\n"
    "Выберите, что хотели бы начать отслеживать"
)

_SERVICE_TITLES = {"bars": "БАРС", "osep": "ОСЭП"}


def service_title(service: str) -> str:
    return _SERVICE_TITLES.get(service, service)


# шарики статуса; кастомные эмодзи — через STATUS_EMOJI_UP/STATUS_EMOJI_DOWN
# в .env: обычный эмодзи (🟢) либо числовой id премиум-эмодзи (переливающийся)
def _status_emoji(up: bool) -> str:
    raw = os.environ.get("STATUS_EMOJI_UP" if up else "STATUS_EMOJI_DOWN", "")
    if not raw:
        return "🟢" if up else "🔴"
    if raw.isdigit():  # id премиум-эмодзи — для переливающихся статусов
        return f'<tg-emoji id="{raw}">{"🟢" if up else "🔴"}</tg-emoji>'
    return raw


def fmt_datetime(value: datetime | None) -> str:
    return fmt_dt(value)


def _fmt_time(value: datetime | None) -> str:
    return fmt_time(value)


def service_panel_text(
    service: str,
    *,
    watching: bool,
    available: bool,
    available_since: datetime | None,
    last_check: datetime | None,
    login: str | None,
) -> str:
    title = _SERVICE_TITLES.get(service, service)
    status = f"{_status_emoji(available)} {'работает' if available else 'не работает'}"
    if watching:
        return (
            f"Отслеживание {title}: ✅ Включено\n\n"
            f"Состояние {title}: {status}"
            f"{'' if available else f' (с {_fmt_time(available_since)})'}\n\n"
            f"Время последней проверки: {fmt_datetime(last_check)}\n\n"
            f"Логин: {login or 'Нет'}"
        )
    return (
        f"Отслеживание {title}: ❌ Выключено\n\n"
        f"Состояние {title}: {status}\n\n"
        f"Логин: {login or 'Нет'}\n\n"
        f"Если {title} не работает, временно не получится подключить эту функцию"
    )


def bars_settings_text(
    hidden_mark: bool,
    notify_absences: bool,
    reminders: bool,
    *,
    is_journal_responsible: bool = False,
    reminder_time: time | None = None,
) -> str:
    lines = [
        "Настройки для БАРС:",
        "• Уведомления об оценках: "
        + ("оценка скрывается (спойлер)" if hidden_mark else "оценка показывается"),
        "• Уведомления о пропусках: "
        + ("включены" if notify_absences else "выключены"),
    ]
    # «Пары» — только ответственному за журнал (после логина становится ясно)
    if is_journal_responsible:
        lines.append(
            "• Уведомления о проставлении пар: "
            f"{'включены' if reminders else 'выключены'} "
            f"(в {fmt_clock(reminder_time) if reminder_time else '18:00'} по GMT+3)"
        )
    return "\n\n".join(lines)


def osep_settings_text(blacklist: list | None) -> str:
    entries = blacklist or []
    listed = "\n".join(f"• {pattern}" for pattern in entries) or "— пусто"
    return (
        "Настройки для ОСЭП:\n\n"
        "Чёрный список (не отправлять сообщения от конкретных отправителей, "
        "поддерживается *):\n" + listed
    )


def services_text(bars: tuple[bool, datetime | None], osep: tuple[bool, datetime | None]) -> str:
    """Статусы обоих сервисов; у недоступного — с какого момента (дата, МСК)."""

    def line(title: str, pair: tuple[bool, datetime | None]) -> str:
        up, since = pair
        if up:
            return f"{title}: {_status_emoji(True)} доступен"
        return (
            f"{title}: {_status_emoji(False)} не доступен "
            f"(с {fmt_datetime(since)} МСК)"
        )

    return "\n\n".join([line("БАРС", bars), line("ОСЭП", osep)])


def statistic_text(total: int, with_bars: int, with_osep: int) -> str:
    return (
        f"Всего людей в боте: {total}\n\n"
        f"Используют БАРС: {with_bars}\n\n"
        f"Используют ОСЭП: {with_osep}"
    )


def is_watching(state_status: WatchStates | None) -> bool:
    return state_status == WatchStates.ACTIVE
