from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton


def get_start_keyboard():
    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(text="Оповещения БАРС", callback_data='bars'),
        InlineKeyboardButton(text="Оповещения ОСЭП", callback_data='osep')
    )

    return keyboard.as_markup()

def input_bars_data_keyboard():
    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        # InlineKeyboardButton(text="Ввести логин", callback_data='bars_login'),
        # InlineKeyboardButton(text="Ввести пароль", callback_data='bars_password')
        InlineKeyboardButton(text="Ввести данные для входа в БАРС", callback_data='bars_credentials'),
    )
    return keyboard.as_markup()

def input_osep_data_keyboard():
    keyboard = InlineKeyboardBuilder()
    keyboard.add(
        InlineKeyboardButton(text="Ввести данные для входа в ОСЭП", callback_data='osep_credentials'),
    )
    return keyboard.as_markup()

def update_bars_data_keyboard(used_bars: bool = False):
    keyboard = InlineKeyboardBuilder()
    if used_bars:
        keyboard.row(
            InlineKeyboardButton(text="Перестать отслеживать БАРС", callback_data='dont_watching_bars'),
        )
    else:
        keyboard.row(
            InlineKeyboardButton(text="Отслеживать БАРС", callback_data='watching_bars'),
        )

    keyboard.row(
        InlineKeyboardButton(text="Обновить данные для входа в БАРС", callback_data='bars_credentials'),
    )
    return keyboard.as_markup()

def update_osep_data_keyboard(used_osep: bool = False):
    keyboard = InlineKeyboardBuilder()
    if used_osep:
        keyboard.row(
            InlineKeyboardButton(text="Перестать отслеживать ОСЭП", callback_data='dont_watching_osep'),
        )
    else:
        keyboard.row(
            InlineKeyboardButton(text="Отслеживать ОСЭП", callback_data='watching_osep'),
        )

    keyboard.row(
        InlineKeyboardButton(text="Обновить данные для входа в ОСЭП", callback_data='osep_credentials'),
    )
    return keyboard.as_markup()