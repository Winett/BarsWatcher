from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from filters.admin import AdminFilter
from states.configState import ConfigState
from database.db import async_session
from database.services.config_service import ConfigService
from watchers.managers.watcher_manager import BarsWatcherManager, OsepWatcherManager

from loguru import logger

router = Router(name=__name__)

GLOBAL_PARAMS = {
    "poll_interval": {"type": int, "min": 60, "max": 3600, "desc": "Интервал опроса (сек)"},
    "max_poll_interval": {"type": int, "min": 60, "max": 3600, "desc": "Макс. интервал авто-шкалирования (сек)"},
    "timeout": {"type": int, "min": 5, "max": 120, "desc": "Таймаут запроса (сек)"},
    "stagger_delay": {"type": float, "min": 0.5, "max": 30, "desc": "Задержка между вотчерами (сек)"},
    "stagger_jitter": {"type": float, "min": 0, "max": 30, "desc": "Разброс задержки (сек)"},
    "auto_scale_enabled": {"type": bool, "min": None, "max": None, "desc": "Авто-шкалирование"},
}

USER_PARAMS = {
    "poll_interval": {"type": int, "min": 60, "max": 3600, "desc": "Интервал опроса (сек)"},
    "auto_scale_enabled": {"type": bool, "min": None, "max": None, "desc": "Авто-шкалирование"},
}


def _fmt(val, param_info):
    if param_info["type"] == bool:
        return "вкл" if val else "выкл"
    return str(val)


def _format_global_config(cfg) -> str:
    return (
        "⚙️ Глобальные настройки:\n\n"
        f"• poll_interval: <b>{cfg.poll_interval}</b> сек\n"
        f"• max_poll_interval: <b>{cfg.max_poll_interval}</b> сек\n"
        f"• timeout: <b>{cfg.timeout}</b> сек\n"
        f"• stagger_delay: <b>{cfg.stagger_delay}</b> сек\n"
        f"• stagger_jitter: <b>{cfg.stagger_jitter}</b> сек\n"
        f"• auto_scale: <b>{'вкл' if cfg.auto_scale_enabled else 'выкл'}</b>\n"
        f"• user_poll_mode: <b>{cfg.user_poll_mode}</b>"
    )


def _build_global_params_keyboard(cfg) -> InlineKeyboardMarkup:
    buttons = []
    for key, info in GLOBAL_PARAMS.items():
        val = _fmt(getattr(cfg, key), info)
        buttons.append([InlineKeyboardButton(
            text=f"{info['desc']}: {val}",
            callback_data=f"cfg_g_{key}"
        )])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _build_user_params_keyboard(user_cfg, global_cfg) -> InlineKeyboardMarkup:
    buttons = []
    for key, info in USER_PARAMS.items():
        user_val = getattr(user_cfg, key, None) if user_cfg else None
        if user_val is not None:
            val = _fmt(user_val, info)
            label = f"{info['desc']}: {val}"
        else:
            val = _fmt(getattr(global_cfg, key), info)
            label = f"{info['desc']}: {val} (глобальный)"
        buttons.append([InlineKeyboardButton(
            text=label,
            callback_data=f"cfg_u_{key}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="cfg_cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# === /config_show ===

@router.message(AdminFilter(), Command('config_show'))
async def config_show_command(msg: Message):
    cs = ConfigService(async_session)
    global_cfg = await cs.get_global()

    from sqlalchemy import select, func
    from database.models import UserConfig
    async with async_session() as session:
        result = await session.execute(select(func.count(UserConfig.id)))
        user_configs_count = result.scalar() or 0

    text = _format_global_config(global_cfg)
    text += f"\n\n👤 Персональных Override: <b>{user_configs_count}</b> шт."
    await msg.answer(text, parse_mode="HTML")


# === /config_global ===

@router.message(AdminFilter(), Command('config_global'))
async def config_global_command(msg: Message, state: FSMContext):
    cs = ConfigService(async_session)
    global_cfg = await cs.get_global()

    await msg.answer(
        "⚙️ Выберите параметр для изменения:\n",
        reply_markup=_build_global_params_keyboard(global_cfg),
        parse_mode="HTML"
    )
    await state.set_state(ConfigState.waiting_param)


@router.callback_query(F.data.startswith("cfg_g_"))
async def config_global_param_callback(callback: CallbackQuery, state: FSMContext):
    param = callback.data[6:]  # "cfg_g_" = 6 символов

    if param not in GLOBAL_PARAMS:
        await callback.answer("Неизвестный параметр", show_alert=True)
        return

    await state.update_data(param=param)
    param_info = GLOBAL_PARAMS[param]

    cs = ConfigService(async_session)
    global_cfg = await cs.get_global()
    current_value = getattr(global_cfg, param)

    type_name = "число" if param_info["type"] == int else "дробное число" if param_info["type"] == float else "вкл/выкл"
    range_text = f"\nДиапазон: {param_info['min']} — {param_info['max']}" if param_info["min"] is not None else ""

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data="cfg_g_back")]
    ])

    await callback.message.edit_text(
        f"⚙️ Параметр: <b>{param}</b>\n"
        f"Текущее значение: <b>{_fmt(current_value, param_info)}</b>\n"
        f"Тип: {type_name}{range_text}\n\n"
        f"Введите новое значение:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await state.set_state(ConfigState.waiting_value)
    await callback.answer()


@router.callback_query(F.data == "cfg_g_back")
async def config_global_back_callback(callback: CallbackQuery, state: FSMContext):
    cs = ConfigService(async_session)
    global_cfg = await cs.get_global()

    await callback.message.edit_text(
        "⚙️ Выберите параметр для изменения:\n",
        reply_markup=_build_global_params_keyboard(global_cfg),
        parse_mode="HTML"
    )
    await state.set_state(ConfigState.waiting_param)
    await callback.answer()


@router.message(AdminFilter(), ConfigState.waiting_value)
async def config_global_value_received(msg: Message, state: FSMContext):
    data = await state.get_data()
    param = data["param"]
    param_info = GLOBAL_PARAMS[param]

    try:
        if param_info["type"] == bool:
            value = msg.text.strip().lower() in ("true", "1", "yes", "да", "вкл", "on")
        elif param_info["type"] == int:
            value = int(msg.text.strip())
        elif param_info["type"] == float:
            value = float(msg.text.strip())
        else:
            await msg.answer("Неизвестный тип параметра.")
            await state.clear()
            return
    except ValueError:
        hint = "вкл/выкл" if param_info["type"] == bool else "число"
        await msg.answer(f"Неверное значение. Введите {hint}.")
        return

    if param_info["min"] is not None and value < param_info["min"]:
        await msg.answer(f"Значение не может быть меньше {param_info['min']}.")
        return
    if param_info["max"] is not None and value > param_info["max"]:
        await msg.answer(f"Значение не может быть больше {param_info['max']}.")
        return

    cs = ConfigService(async_session)
    await cs.set_global(**{param: value})

    await BarsWatcherManager.refresh_all_configs()
    await OsepWatcherManager.refresh_all_configs()

    await msg.answer(
        f"✅ Параметр <b>{param}</b> изменён на <b>{_fmt(value, param_info)}</b>",
        parse_mode="HTML"
    )
    await state.clear()
    logger.info(f"Admin: global config changed — {param}={value}")


# === /config_user ===

@router.message(AdminFilter(), Command('config_user'))
async def config_user_command(msg: Message, state: FSMContext):
    await msg.answer("👤 Введите user_id пользователя:")
    await state.set_state(ConfigState.waiting_user_id)


@router.message(AdminFilter(), ConfigState.waiting_user_id)
async def config_user_id_received(msg: Message, state: FSMContext):
    try:
        user_id = int(msg.text.strip())
    except ValueError:
        await msg.answer("Неверный user_id. Введите число.")
        return

    await state.update_data(user_id=user_id)

    cs = ConfigService(async_session)
    user_cfg = await cs.get_user(user_id)
    global_cfg = await cs.get_global()

    # Показываем текущие настройки пользователя
    lines = []
    for key, info in USER_PARAMS.items():
        user_val = getattr(user_cfg, key, None) if user_cfg else None
        if user_val is not None:
            lines.append(f"• {info['desc']}: <b>{_fmt(user_val, info)}</b>")
        else:
            lines.append(f"• {info['desc']}: <b>{_fmt(getattr(global_cfg, key), info)}</b> (глобальный)")

    text = (
        f"👤 Пользователь: <b>{user_id}</b>\n\n"
        f"Текущие настройки:\n" + "\n".join(lines) +
        "\n\nНажмите параметр для изменения:"
    )

    await msg.answer(
        text,
        reply_markup=_build_user_params_keyboard(user_cfg, global_cfg),
        parse_mode="HTML"
    )
    await state.set_state(ConfigState.waiting_user_param)


@router.callback_query(F.data.startswith("cfg_u_"))
async def config_user_param_callback(callback: CallbackQuery, state: FSMContext):
    param = callback.data[6:]  # "cfg_u_" = 6 символов

    if param not in USER_PARAMS:
        await callback.answer("Неизвестный параметр", show_alert=True)
        return

    data = await state.get_data()
    user_id = data.get("user_id")
    if not user_id:
        await callback.answer("Ошибка: user_id не найден", show_alert=True)
        await state.clear()
        return

    await state.update_data(param=param)
    param_info = USER_PARAMS[param]

    cs = ConfigService(async_session)
    user_cfg = await cs.get_user(user_id)
    global_cfg = await cs.get_global()

    user_val = getattr(user_cfg, param, None) if user_cfg else None
    global_val = getattr(global_cfg, param)

    type_name = "число" if param_info["type"] == int else "дробное число" if param_info["type"] == float else "вкл/выкл"
    range_text = f"\nДиапазон: {param_info['min']} — {param_info['max']}" if param_info["min"] is not None else ""

    if user_val is not None:
        current_display = _fmt(user_val, param_info)
    else:
        current_display = f"{_fmt(global_val, param_info)} (глобальный)"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔙 Назад", callback_data=f"cfg_u_back")]
    ])

    await callback.message.edit_text(
        f"👤 Пользователь: <b>{user_id}</b>\n"
        f"⚙️ Параметр: <b>{param}</b>\n"
        f"Текущее значение: <b>{current_display}</b>\n"
        f"Тип: {type_name}{range_text}\n\n"
        f"Введите новое значение (или «сброс» для возврата к глобальному):",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await state.set_state(ConfigState.waiting_user_value)
    await callback.answer()


@router.callback_query(F.data == "cfg_u_back")
async def config_user_back_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    user_id = data.get("user_id")
    if not user_id:
        await state.clear()
        await callback.answer("Ошибка", show_alert=True)
        return

    cs = ConfigService(async_session)
    user_cfg = await cs.get_user(user_id)
    global_cfg = await cs.get_global()

    lines = []
    for key, info in USER_PARAMS.items():
        user_val = getattr(user_cfg, key, None) if user_cfg else None
        if user_val is not None:
            lines.append(f"• {info['desc']}: <b>{_fmt(user_val, info)}</b>")
        else:
            lines.append(f"• {info['desc']}: <b>{_fmt(getattr(global_cfg, key), info)}</b> (глобальный)")

    text = (
        f"👤 Пользователь: <b>{user_id}</b>\n\n"
        f"Текущие настройки:\n" + "\n".join(lines) +
        "\n\nНажмите параметр для изменения:"
    )

    await callback.message.edit_text(
        text,
        reply_markup=_build_user_params_keyboard(user_cfg, global_cfg),
        parse_mode="HTML"
    )
    await state.set_state(ConfigState.waiting_user_param)
    await callback.answer()


@router.callback_query(F.data.startswith("cfg_u_reset_"))
async def config_user_reset_callback(callback: CallbackQuery, state: FSMContext):
    user_id = int(callback.data[12:])  # "cfg_u_reset_" = 12 символов

    cs = ConfigService(async_session)
    deleted = await cs.reset_user(user_id)

    watcher = BarsWatcherManager.get_watcher_instance(user_id)
    if watcher:
        await watcher.refresh_config()
    watcher = OsepWatcherManager.get_watcher_instance(user_id)
    if watcher:
        await watcher.refresh_config()

    if deleted:
        await callback.message.edit_text(
            f"✅ Персональный конфиг пользователя {user_id} удалён. Используется глобальный."
        )
    else:
        await callback.answer(f"У пользователя {user_id} нет персонального конфига.", show_alert=True)

    await state.clear()
    await callback.answer()


@router.message(AdminFilter(), ConfigState.waiting_user_value)
async def config_user_value_received(msg: Message, state: FSMContext):
    data = await state.get_data()
    user_id = data["user_id"]
    param = data["param"]
    param_info = USER_PARAMS[param]

    if msg.text.strip().lower() in ("сброс", "reset", "delete", "удалить"):
        cs = ConfigService(async_session)
        deleted = await cs.reset_user(user_id)

        if deleted:
            watcher = BarsWatcherManager.get_watcher_instance(user_id)
            if watcher:
                await watcher.refresh_config()
            watcher = OsepWatcherManager.get_watcher_instance(user_id)
            if watcher:
                await watcher.refresh_config()
            await msg.answer(f"✅ Персональный конфиг пользователя {user_id} удалён.")
        else:
            await msg.answer(f"У пользователя {user_id} нет персонального конфига.")
        await state.clear()
        return

    try:
        if param_info["type"] == bool:
            value = msg.text.strip().lower() in ("true", "1", "yes", "да", "вкл", "on")
        elif param_info["type"] == int:
            value = int(msg.text.strip())
        elif param_info["type"] == float:
            value = float(msg.text.strip())
        else:
            await msg.answer("Неизвестный тип параметра.")
            await state.clear()
            return
    except ValueError:
        hint = "вкл/выкл" if param_info["type"] == bool else "число"
        await msg.answer(f"Неверное значение. Введите {hint}.")
        return

    if param_info["min"] is not None and value < param_info["min"]:
        await msg.answer(f"Значение не может быть меньше {param_info['min']}.")
        return
    if param_info["max"] is not None and value > param_info["max"]:
        await msg.answer(f"Значение не может быть больше {param_info['max']}.")
        return

    cs = ConfigService(async_session)
    await cs.set_user(user_id, **{param: value})

    watcher = BarsWatcherManager.get_watcher_instance(user_id)
    if watcher:
        await watcher.refresh_config()
    watcher = OsepWatcherManager.get_watcher_instance(user_id)
    if watcher:
        await watcher.refresh_config()

    await msg.answer(
        f"✅ Параметр <b>{param}</b> для пользователя <b>{user_id}</b> изменён на <b>{_fmt(value, param_info)}</b>",
        parse_mode="HTML"
    )
    await state.clear()
    logger.info(f"Admin: user config changed — user={user_id} {param}={value}")


@router.callback_query(F.data == "cfg_cancel")
async def config_cancel_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Отменено.")
    await callback.answer()
