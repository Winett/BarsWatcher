"""Инлайн-клавиатуры бота (по схеме BarsWatcherBotLogic.drawio)."""

from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

__all__ = [
    "main_menu",
    "service_panel",
    "bars_settings_kb",
    "osep_settings_kb",
    "two_fa_prompt_kb",
    "two_fa_input_kb",
]


def main_menu() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📗 БАРС", callback_data="menu:bars")
    kb.button(text="📧 ОСЭП", callback_data="menu:osep")
    kb.adjust(2)
    return kb.as_markup()


def service_panel(
    service: str,
    *,
    watching: bool,
    available: bool,
) -> InlineKeyboardMarkup:
    """Панель сервиса: БАРС или ОСЭП."""
    kb = InlineKeyboardBuilder()
    prefix = f"panel:{service}"
    if watching:
        kb.button(text="🔄 Обновить", callback_data=f"{prefix}:refresh")
        kb.button(text="⛔ Отключить", callback_data=f"{prefix}:disable")
    elif available:
        kb.button(text="✅ Включить", callback_data=f"{prefix}:enable")
        kb.button(text="🔄 Обновить", callback_data=f"{prefix}:refresh")
    else:
        kb.button(text="⏸ Сервис недоступен", callback_data=f"{prefix}:na")
        kb.button(text="🔄 Обновить", callback_data=f"{prefix}:refresh")
    kb.button(text="🔑 Изменить данные для входа", callback_data=f"{prefix}:creds")
    kb.button(text="⚙️ Настройки", callback_data=f"{prefix}:settings")
    kb.button(text="⬅️ Назад", callback_data="menu:home")
    kb.adjust(1)
    return kb.as_markup()


def bars_settings_kb(
    *,
    hidden_mark: bool,
    notify_absences: bool,
    reminders: bool,
    is_journal_responsible: bool = False,
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(
        text=f"📗 Оценки: {'скрывать (спойлер)' if hidden_mark else 'показывать'}",
        callback_data="set:bars:hidden",
    )
    kb.button(
        text=f"🚶 Пропуски: {'вкл' if notify_absences else 'выкл'}",
        callback_data="set:bars:absences",
    )
    # «Пары» — только ответственному за журнал (после логина видно, кто он)
    if is_journal_responsible:
        kb.button(
            text=f"⏰ Пары: {'вкл' if reminders else 'выкл'}",
            callback_data="set:bars:reminders",
        )
        kb.button(
            text="📋 Проверить пары за сегодня",
            callback_data="bars:pairs",
        )
        kb.button(
            text="🔍 Сверка пар с расписанием",
            callback_data="bars:lessons",
        )
    kb.button(text="⬅️ Назад", callback_data="menu:bars")
    kb.adjust(1)
    return kb.as_markup()


def osep_settings_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🚫 Добавить в чёрный список", callback_data="set:osep:bl_add")
    kb.button(text="📋 Чёрный список", callback_data="set:osep:bl_show")
    kb.button(text="⬅️ Назад", callback_data="menu:osep")
    kb.adjust(1)
    return kb.as_markup()


def two_fa_prompt_kb(service: str) -> InlineKeyboardMarkup:
    """Кнопка в уведомлении о 2FA: код вводится по желанию пользователя."""
    kb = InlineKeyboardBuilder()
    kb.button(text="⌨️ Ввести код 2FA", callback_data=f"2fa:{service}:input")
    kb.adjust(1)
    return kb.as_markup()


def two_fa_input_kb(service: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="🔁 Отправить код снова", callback_data=f"2fa:{service}:resend")
    kb.button(text="❌ Отмена", callback_data=f"2fa:{service}:cancel")
    kb.adjust(2)
    return kb.as_markup()


def confirm_kb(service: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Да", callback_data=f"creds:{service}:start")
    kb.button(text="❌ Нет", callback_data=f"menu:{service}")
    kb.adjust(2)
    return kb.as_markup()


def back_kb(target: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="⬅️ Назад", callback_data=target)
    return kb.as_markup()
