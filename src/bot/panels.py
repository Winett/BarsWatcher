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


def _state(value) -> WatchStates | None:
    """Нормализовать статус из БД (на случай строкового значения)."""
    if value is None:
        return None
    if isinstance(value, WatchStates):
        return value
    try:
        return WatchStates(str(value).lower())
    except ValueError:
        return None


def service_panel_text(
    service: str,
    *,
    watching: bool,
    available: bool,
    available_since: datetime | None,
    last_check: datetime | None,
    login: str | None,
    status: WatchStates | str | None = None,
) -> str:
    """Текст панели. Статус показывается явно: «ожидает 2FA» или «неверный
    логин» — это НЕ «выключено», хотя проверки и приостановлены."""
    title = _SERVICE_TITLES.get(service, service)
    state = _state(status)
    link = f"{_status_emoji(available)} {'работает' if available else 'не работает'}"
    link_line = f"Состояние {title}: {link}"
    if not available:
        link_line += f" (с {_fmt_time(available_since)})"
    footer = (
        f"{link_line}\n\n"
        f"Время последней проверки: {fmt_datetime(last_check)}\n\n"
        f"Логин: {login or 'Нет'}"
    )

    if watching or state == WatchStates.ACTIVE:
        head = f"Отслеживание {title}: ✅ Включено\n\n"
    elif state == WatchStates.WAITING_2FA:
        head = (
            f"Отслеживание {title}: 🔐 Требуется код 2FA\n\n"
            "Проверки приостановлены, пока код не введён. Нажмите «✅ Включить» "
            "и следуйте подсказке — или используйте кнопку из уведомления.\n\n"
        )
    elif state == WatchStates.INVALID_CREDS:
        head = (
            f"Отслеживание {title}: ❌ Неверный логин или пароль\n\n"
            "Обновите данные кнопкой «🔑 Изменить данные для входа».\n\n"
        )
    elif state == WatchStates.INITIAL:
        head = (
            f"Отслеживание {title}: ⚪ Не настроено\n\n"
            "Нажмите «✅ Включить», чтобы начать отслеживание.\n\n"
        )
    else:
        head = (
            f"Отслеживание {title}: ❌ Выключено\n\n"
            f"Если {title} не работает, временно не получится подключить эту функцию.\n\n"
        )
    return head + footer


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


def statistic_text(
    total: int,
    with_bars: int,
    with_osep: int,
    active_bars: int = 0,
    active_osep: int = 0,
) -> str:
    """Данные — у кого введён логин; используют — у кого включено отслеживание."""
    return (
        f"Всего людей в боте: {total}\n\n"
        f"Данных БАРС: {with_bars}\n"
        f"Используют БАРС: {active_bars}\n\n"
        f"Данных ОСЭП: {with_osep}\n"
        f"Используют ОСЭП: {active_osep}"
    )


def is_watching(state_status: WatchStates | None) -> bool:
    return state_status == WatchStates.ACTIVE
