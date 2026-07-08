import json
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from sqlalchemy.orm import sessionmaker
from database.db import async_session
from database.services.config_service import ConfigService
from services.user import UserService

from loguru import logger

router = Router(name=__name__)


class SettingsState(StatesGroup):
    waiting_service = State()
    waiting_bars_param = State()
    waiting_bars_value = State()
    waiting_osep_param = State()
    waiting_osep_blacklist_action = State()
    waiting_osep_blacklist_pattern = State()


# === /settings ===

@router.message(Command('settings'))
async def settings_command(msg: Message, state: FSMContext):
    user_id = msg.from_user.id

    cs = ConfigService(async_session)
    user_cfg = await cs.get_user(user_id)

    buttons = []

    # БАРС
    async with async_session() as session:
        user_service = UserService(session)
        used_bars = await user_service.check_bars(user_id)

    if used_bars:
        bars_val = getattr(user_cfg, 'bars_show_marks', None) if user_cfg else None
        bars_status = "вкл" if bars_val is not False else "выкл" if bars_val is not None else "вкл"
        buttons.append([InlineKeyboardButton(
            text=f"📊 БАРС (оценки: {bars_status})",
            callback_data="usr_settings_bars"
        )])

    # ОСЭП
    async with async_session() as session:
        user_service = UserService(session)
        used_osep = await user_service.check_osep(user_id)

    if used_osep:
        osep_cfg = await cs.resolve_osep_config(user_id)
        bl_count = len(osep_cfg.blacklist)
        bl_text = f" (blacklist: {bl_count})" if bl_count else ""
        buttons.append([InlineKeyboardButton(
            text=f"📧 ОСЭП{bl_text}",
            callback_data="usr_settings_osep"
        )])

    if not buttons:
        await msg.answer(
            "⚙️ Настройки\n\n"
            "У вас нет активных сервисов. Сначала включите отслеживание БАРС или ОСЭП."
        )
        return

    buttons.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="usr_settings_close")])

    await msg.answer(
        "⚙️ <b>Настройки</b>\n\nВыберите сервис:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await state.set_state(SettingsState.waiting_service)


# === БАРС настройки ===

@router.callback_query(F.data == "usr_settings_bars")
async def settings_bars_callback(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    cs = ConfigService(async_session)
    user_cfg = await cs.get_user(user_id)

    user_val = getattr(user_cfg, 'bars_show_marks', None) if user_cfg else None
    current = "вкл" if user_val is not False else "выкл" if user_val is not None else "вкл"
    source = "" if user_val is not None else " (по умолчанию)"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Показывать оценки сразу: {current}{source}",
            callback_data="usr_bars_toggle_show_marks"
        )],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="usr_settings_back")]
    ])

    await callback.message.edit_text(
        "📊 <b>Настройки БАРС</b>\n\nНажмите параметр для изменения:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await state.set_state(SettingsState.waiting_bars_param)
    await callback.answer()


@router.callback_query(F.data == "usr_bars_toggle_show_marks")
async def settings_bars_toggle_callback(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    cs = ConfigService(async_session)
    user_cfg = await cs.get_user(user_id)

    current_val = getattr(user_cfg, 'bars_show_marks', None) if user_cfg else None
    if current_val is None:
        current_val = True  # дефолт

    new_val = not current_val
    await cs.set_user(user_id, bars_show_marks=new_val)

    # Обновить конфиг вотчера
    from watchers.managers.watcher_manager import BarsWatcherManager
    watcher = BarsWatcherManager.get_watcher_instance(user_id)
    if watcher:
        watcher.bars_config.show_marks = new_val

    display = "вкл" if new_val else "выкл"
    await callback.answer(f"Показывать оценки: {display}", show_alert=True)

    # Обновить клавиатуру
    user_cfg = await cs.get_user(user_id)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"Показывать оценки сразу: {display}",
            callback_data="usr_bars_toggle_show_marks"
        )],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="usr_settings_back")]
    ])

    await callback.message.edit_reply_markup(reply_markup=keyboard)
    logger.info(f"User {user_id}: bars_show_marks changed to {new_val}")


# === ОСЭП настройки ===

@router.callback_query(F.data == "usr_settings_osep")
async def settings_osep_callback(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    cs = ConfigService(async_session)
    osep_cfg = await cs.resolve_osep_config(user_id)

    if osep_cfg.blacklist:
        bl_list = "\n".join([f"  • <code>{p}</code>" for p in osep_cfg.blacklist])
        bl_text = f"\n\n🚫 <b>Blacklist:</b>\n{bl_list}"
    else:
        bl_text = "\n\n🚫 Blacklist: пусто"

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить в blacklist", callback_data="usr_osep_bl_add")],
        [InlineKeyboardButton(text="🗑 Удалить из blacklist", callback_data="usr_osep_bl_remove")],
        [InlineKeyboardButton(text="🔙 Назад", callback_data="usr_settings_back")]
    ])

    await callback.message.edit_text(
        f"📧 <b>Настройки ОСЭП</b>{bl_text}\n\nВыберите действие:",
        reply_markup=keyboard,
        parse_mode="HTML"
    )
    await state.set_state(SettingsState.waiting_osep_param)
    await callback.answer()


@router.callback_query(F.data == "usr_osep_bl_add")
async def settings_osep_bl_add_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "📧 Введите паттерн email для blacklist:\n\n"
        "Примеры:\n"
        "  • <code>*@spam.com</code> — все письма с @spam.com\n"
        "  • <code>newsletter@*</code> — все newsletter'ы\n"
        "  • <code>*@noreply.*</code> — автоматические уведомления\n\n"
        "Поддерживается wildcard <code>*</code> (заменяет любые символы).",
        parse_mode="HTML"
    )
    await state.set_state(SettingsState.waiting_osep_blacklist_pattern)
    await callback.answer()


@router.message(SettingsState.waiting_osep_blacklist_pattern)
async def settings_osep_bl_pattern_received(msg: Message, state: FSMContext):
    pattern = msg.text.strip()
    user_id = msg.from_user.id

    if not pattern:
        await msg.answer("Паттерн не может быть пустым. Попробуйте снова.")
        return

    cs = ConfigService(async_session)
    osep_cfg = await cs.resolve_osep_config(user_id)

    if pattern in osep_cfg.blacklist:
        await msg.answer(f"Паттерн <code>{pattern}</code> уже есть в blacklist.", parse_mode="HTML")
        return

    new_blacklist = osep_cfg.blacklist + [pattern]
    await cs.set_user(user_id, osep_blacklist=json.dumps(new_blacklist))

    # Обновить конфиг вотчера
    from watchers.managers.watcher_manager import OsepWatcherManager
    watcher = OsepWatcherManager.get_watcher_instance(user_id)
    if watcher:
        watcher.osep_config.blacklist = new_blacklist

    await msg.answer(f"✅ Паттерн <code>{pattern}</code> добавлен в blacklist.", parse_mode="HTML")
    await state.clear()
    logger.info(f"User {user_id}: osep blacklist added '{pattern}'")


@router.callback_query(F.data == "usr_osep_bl_remove")
async def settings_osep_bl_remove_callback(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    cs = ConfigService(async_session)
    osep_cfg = await cs.resolve_osep_config(user_id)

    if not osep_cfg.blacklist:
        await callback.answer("Blacklist пуст.", show_alert=True)
        return

    buttons = []
    for i, pattern in enumerate(osep_cfg.blacklist):
        buttons.append([InlineKeyboardButton(
            text=f"🗑 {pattern}",
            callback_data=f"usr_osep_bl_del_{i}"
        )])
    buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="usr_settings_osep")])

    await callback.message.edit_text(
        "🗑 Выберите паттерн для удаления:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await state.set_state(SettingsState.waiting_osep_blacklist_action)
    await callback.answer()


@router.callback_query(F.data.startswith("usr_osep_bl_del_"))
async def settings_osep_bl_delete_callback(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data[16:])  # "usr_osep_bl_del_" = 16
    user_id = callback.from_user.id

    cs = ConfigService(async_session)
    osep_cfg = await cs.resolve_osep_config(user_id)

    if idx >= len(osep_cfg.blacklist):
        await callback.answer("Ошибка: индекс вне диапазона", show_alert=True)
        return

    removed = osep_cfg.blacklist[idx]
    new_blacklist = [p for i, p in enumerate(osep_cfg.blacklist) if i != idx]
    await cs.set_user(user_id, osep_blacklist=json.dumps(new_blacklist) if new_blacklist else None)

    # Обновить конфиг вотчера
    from watchers.managers.watcher_manager import OsepWatcherManager
    watcher = OsepWatcherManager.get_watcher_instance(user_id)
    if watcher:
        watcher.osep_config.blacklist = new_blacklist

    await callback.answer(f"Удалён: {removed}", show_alert=True)

    # Вернуться к списку
    if new_blacklist:
        buttons = []
        for i, pattern in enumerate(new_blacklist):
            buttons.append([InlineKeyboardButton(
                text=f"🗑 {pattern}",
                callback_data=f"usr_osep_bl_del_{i}"
            )])
        buttons.append([InlineKeyboardButton(text="🔙 Назад", callback_data="usr_settings_osep")])
        await callback.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))
    else:
        await callback.message.edit_text(
            "📧 <b>Настройки ОСЭП</b>\n\n🚫 Blacklist: пусто",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Добавить в blacklist", callback_data="usr_osep_bl_add")],
                [InlineKeyboardButton(text="🔙 Назад", callback_data="usr_settings_back")]
            ]),
            parse_mode="HTML"
        )

    await state.set_state(SettingsState.waiting_osep_blacklist_action)
    logger.info(f"User {user_id}: osep blacklist removed '{removed}'")


# === Назад / Закрыть ===

@router.callback_query(F.data == "usr_settings_back")
async def settings_back_callback(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    cs = ConfigService(async_session)
    user_cfg = await cs.get_user(user_id)

    buttons = []

    async with async_session() as session:
        user_service = UserService(session)
        used_bars = await user_service.check_bars(user_id)

    if used_bars:
        bars_val = getattr(user_cfg, 'bars_show_marks', None) if user_cfg else None
        bars_status = "вкл" if bars_val is not False else "выкл" if bars_val is not None else "вкл"
        buttons.append([InlineKeyboardButton(
            text=f"📊 БАРС (оценки: {bars_status})",
            callback_data="usr_settings_bars"
        )])

    async with async_session() as session:
        user_service = UserService(session)
        used_osep = await user_service.check_osep(user_id)

    if used_osep:
        osep_cfg = await cs.resolve_osep_config(user_id)
        bl_count = len(osep_cfg.blacklist)
        bl_text = f" (blacklist: {bl_count})" if bl_count else ""
        buttons.append([InlineKeyboardButton(
            text=f"📧 ОСЭП{bl_text}",
            callback_data="usr_settings_osep"
        )])

    buttons.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="usr_settings_close")])

    await callback.message.edit_text(
        "⚙️ <b>Настройки</b>\n\nВыберите сервис:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        parse_mode="HTML"
    )
    await state.set_state(SettingsState.waiting_service)
    await callback.answer()


@router.callback_query(F.data == "usr_settings_close")
async def settings_close_callback(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.delete()
    await callback.answer()
