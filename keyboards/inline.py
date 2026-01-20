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

def confirm_keyboard():
    keyboard = InlineKeyboardBuilder()
    keyboard.row(
        InlineKeyboardButton(text="Подтвердить", callback_data='confirm'),
    )
    return keyboard.as_markup()

def enable_watching_keyboard(user_id: int, used_bars: bool = False, used_osep: bool = False):
    keyboard = InlineKeyboardBuilder()
    if used_bars:
        keyboard.row(
            InlineKeyboardButton(text="Перестать отслеживать БАРС", callback_data=f'dont_watching_bars_{user_id}'),
        )
    else:
        keyboard.row(
            InlineKeyboardButton(text="Отслеживать БАРС", callback_data=f'watching_bars_{user_id}'),
        )

    if used_osep:
        keyboard.row(
            InlineKeyboardButton(text="Перестать отслеживать ОСЭП", callback_data=f'dont_watching_osep_{user_id}'),
        )
    else:
        keyboard.row(
            InlineKeyboardButton(text="Отслеживать ОСЭП", callback_data=f'watching_osep_{user_id}'),
        )
    keyboard.row(
        InlineKeyboardButton(text="Обновить", callback_data=f'refresh_user_state_massage_{user_id}')
    )
    return keyboard.as_markup()