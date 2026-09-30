"""Админка Telegram-бота: глобальные настройки, пользователи, администраторы.

Что умеет (/admin, только для администраторов):
  - глобальные настройки опросов (редактирование значений, авто-масштаб);
  - карточка пользователя: статусы БАРС/ОСЭП, профиль, индивидуальные
    настройки (кастомное перекрывает глобальное по-полево, видно источник);
  - добавление/удаление администраторов;
  - отправка логов за дату inline-кнопками (логи обоих процессов — bot и
    worker пишут в общий logs/); кнопки только за даты, где логи ещё есть;
  - уведомления админам: новые пользователи, статусы сервисов.

Админы при первом запуске импортируются из ADMINS (.env) по telegram_id
(см. src.services.admins.sync_admins_from_settings); дальше правки живут в БД.
"""

from __future__ import annotations

import json
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from loguru import logger

from src.bot.throttle import send_with_retry
from src.core.config import settings
from src.core.timefmt import fmt_dt
from src.db.database import session_factory
from src.db.models import WatchStates
from src.repositories.uow import UnitOfWork
from src.services.admins import (
    add_admin,
    admin_telegram_ids,
    deactivate_user,
    is_admin_user,
    is_env_admin,
    notify_admins,
    remove_admin,
)
from src.services.config_resolver import merge_settings
from src.services.logfiles import build_log_zips, format_size, list_log_dates

log = logger.bind(component="admin")

router = Router(name="admin")

__all__ = ["router", "notify_admins"]

# поля глобальных настроек: имя поля → (заголовок, шаг ввода)
_GLOBAL_INT_FIELDS = {
    "poll_interval_seconds": "Интервал опроса, сек",
    "max_poll_interval_seconds": "Максимальный интервал, сек",
    "request_timeout_seconds": "Таймаут запроса, сек",
    "error_timeout_seconds": "Таймаут ошибки, сек",
}
_GLOBAL_BOOL_FIELDS = {
    "auto_scale_enabled": "Авто-масштабирование интервала",
    "notify_service_status": "Уведомления о статусах сервисов",
    "notify_new_users": "Уведомления о новых пользователях",
}
# индивидуальные настройки пользователя (тогглы)
_USER_TOGGLES = {
    "bars:hidden_mark": ("БАРС · скрывать оценку", "hidden_mark"),
    "bars:notify_marks": ("БАРС · уведомления об оценках", "notify_marks"),
    "bars:notify_absences": ("БАРС · уведомления о пропусках", "notify_absences"),
    "bars:reminders": ("БАРС · напоминания (ответственному)", "reminders_enabled"),
}
# персональные переопределения опроса
_OVERRIDE_INT_FIELDS = {
    "poll_interval_seconds": "Интервал опроса, сек",
    "max_poll_interval_seconds": "Максимальный интервал, сек",
    "request_timeout_seconds": "Таймаут запроса, сек",
    "error_timeout_seconds": "Таймаут ошибки, сек",
}
_STATUS_LABELS = {
    WatchStates.ACTIVE: "🟢 Включено",
    WatchStates.INACTIVE: "⚪ Выключено",
    WatchStates.WAITING_2FA: "🔐 Ожидает 2FA",
    WatchStates.INVALID_CREDS: "❌ Неверные данные",
    WatchStates.INITIAL: "… Не настроено",
}


class AdminStates(StatesGroup):
    edit_global = State()
    find_user = State()
    add_admin = State()
    edit_override = State()
    broadcast = State()


# ─────────────────────────────────── доступ ───────────────────────────────────


async def _require_admin(callback: CallbackQuery | None = None, message: Message | None = None) -> bool:
    """Проверка прав. Обычному пользователю /admin просто не существует."""
    if callback is not None:
        if await is_admin_user(callback.from_user.id):
            return True
        await callback.answer()  # молча, без содержимого
        return False
    return bool(message is not None and await is_admin_user(message.from_user.id))


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ──────────────────────────────────── меню ────────────────────────────────────

_MENU_TEXT = (
    "🛠 <b>Администрирование</b>\n\n"
    "⚙️ Глобальные настройки — интервалы опросов для всех\n"
    "👤 Пользователи — статусы и индивидуальные настройки\n"
    "🛡 Администраторы — добавление/удаление\n"
    "🔔 Уведомления админам — статусы сервисов, новые пользователи\n"
    "📣 Рассылка — сообщение всем активным (с предпросмотром)\n"
    "📜 Логи — архивом (оба процесса: bot и worker)\n\n"
    "Команды администратора:\n"
    "/admin — эта панель\n"
    "/statistic — статистика по пользователям\n"
    "/services — недоступные сервисы (с какого времени МСК)\n"
    "/clearqueue — очистить ожидающие события (bot и worker)\n"
    "/simup, /simdown bars|osep|all — тест доступности сервисов"
)


def _menu_kb() -> InlineKeyboardMarkup:
    return _kb(
        [
            [_btn("⚙️ Глобальные настройки", "adm:glob")],
            [_btn("👤 Пользователи", "adm:users")],
            [_btn("🛡 Администраторы", "adm:admins")],
            [_btn("🔔 Уведомления админам", "adm:notif")],
            [_btn("📣 Рассылка", "adm:bcast")],
            [_btn("📜 Логи", "adm:logs")],
        ]
    )


async def _safe_edit(target: Message, text: str, keyboard: InlineKeyboardMarkup | None) -> None:
    try:
        await target.edit_text(text, reply_markup=keyboard)
    except Exception as exc:
        if "message is not modified" in str(exc):
            return  # контент не изменился — не дублируем сообщением
        log.warning("не удалось отредактировать сообщение: {}", exc)


# ──────────────────────────── глобальные настройки ────────────────────────────


async def _globals_panel(callback: CallbackQuery) -> None:
    async with session_factory() as session:
        uow = UnitOfWork(session)
        row = await uow.config.get_global()
    lines = ["⚙️ <b>Глобальные настройки</b>", ""]
    for field, title in _GLOBAL_INT_FIELDS.items():
        value = getattr(row, field, None) if row else None
        lines.append(f"· {title}: <b>{value if value is not None else '—'}</b>")
    for field, title in _GLOBAL_BOOL_FIELDS.items():
        value = getattr(row, field, None) if row else None
        mark = "вкл" if value else "выкл"
        lines.append(f"· {title}: <b>{mark}</b>")
    lines.append("\nНажмите на настройку, чтобы изменить.")

    rows = [
        [_btn(f"{title} — изменить", f"adm:globset:{field}")]
        for field, title in _GLOBAL_INT_FIELDS.items()
    ]
    rows += [
        [
            _btn(f"{title}: {'вкл' if getattr(row, field, False) else 'выкл'} ⚡", f"adm:globtoggle:{field}")
        ]
        for field, title in _GLOBAL_BOOL_FIELDS.items()
    ]
    rows.append([_btn("◀️ Назад", "adm:menu")])
    await _safe_edit(callback.message, "\n".join(lines), _kb(rows))


# ─────────────────────────── уведомления админам ──────────────────────────────


async def _notif_panel(callback: CallbackQuery) -> None:
    async with session_factory() as session:
        uow = UnitOfWork(session)
        row = await uow.config.get_global()
    status = bool(row.notify_service_status) if row else False
    new_users = bool(row.notify_new_users) if row else True
    text = (
        "🔔 <b>Уведомления админам</b>\n\n"
        f"· Статусы сервисов (сервис поднялся/упал): <b>{'вкл' if status else 'выкл'}</b>\n"
        f"· Новые пользователи: <b>{'вкл' if new_users else 'выкл'}</b>\n"
    )
    await _safe_edit(
        callback.message,
        text,
        _kb(
            [
                [_btn(f"Сервисы поднялся/упал: {'вкл' if status else 'выкл'} ⚡", "adm:notiftoggle:notify_service_status")],
                [_btn(f"Новые пользователи: {'вкл' if new_users else 'выкл'} ⚡", "adm:notiftoggle:notify_new_users")],
                [_btn("◀️ Назад", "adm:menu")],
            ]
        ),
    )


# ──────────────────────────── администраторы ─────────────────────────────────


async def _admins_panel(callback: CallbackQuery) -> None:
    ids = await admin_telegram_ids()
    async with session_factory() as session:
        from sqlalchemy import select

        from src.db.models import User

        rows_db = (
            await session.execute(select(User).where(User.telegram_id.in_(ids)))
        ).scalars().all()
    lines = [f"🛡 <b>Администраторы</b> ({len(ids)})", ""]
    for user in rows_db:
        name = f"@{user.username}" if user.username else "—"
        lock = " 🔒" if is_env_admin(user.telegram_id) else ""
        lines.append(f"· {name} · telegram_id=<code>{user.telegram_id}</code>{lock}")
    if not rows_db:
        lines.append("· (нет)")
    lines.append("\n🔒 — задан в .env (ADMINS), удалить нельзя.")
    await _safe_edit(
        callback.message,
        "\n".join(lines),
        _kb(
            [
                [_btn("➕ Добавить (по telegram_id)", "adm:adminadd")],
                # кнопки удаления — только для админов НЕ из .env
                *[
                    [_btn(f"➖ {user.telegram_id}", f"adm:adminrm:{user.telegram_id}")]
                    for user in rows_db
                    if not is_env_admin(user.telegram_id)
                ],
                [_btn("◀️ Назад", "adm:menu")],
            ]
        ),
    )


# ──────────────────────────────── пользователи ───────────────────────────────


async def _users_panel(callback: CallbackQuery) -> None:
    async with session_factory() as session:
        UnitOfWork(session)
        from sqlalchemy import func, select

        from src.db.models import User

        total = await session.scalar(select(func.count()).select_from(User)) or 0
    text = (
        f"👤 <b>Пользователи</b>: всего {total}\n\n"
        "Введите telegram_id (число) или @username для поиска."
    )
    await _safe_edit(
        callback.message,
        text,
        _kb(
            [
                [_btn("🔎 Найти пользователя", "adm:userfind")],
                [_btn("◀️ Назад", "adm:menu")],
            ]
        ),
    )


def _yes_no(value: bool) -> str:
    return "вкл" if value else "выкл"


async def _user_card_text(user_id: int) -> tuple[str, InlineKeyboardMarkup] | None:
    """Карточка пользователя: (текст, клавиатура) или None, если не найден."""
    async with session_factory() as session:
        uow = UnitOfWork(session)
        user = await uow.users.get_by_id(
            user_id,
            with_bars_credentials=True,
            with_bars_profile=True,
            with_bars_state=True,
            with_bars_settings=True,
            with_osep_credentials=True,
            with_osep_state=True,
            with_osep_settings=True,
        )
        if user is None:
            return None
        global_row = await uow.config.get_global()
        override_row = await uow.config.get_override(user_id)

    name = f"@{user.username}" if user.username else "—"
    lines = [
        f"👤 <b>{name}</b> · id={user.id} · telegram_id=<code>{user.telegram_id}</code>",
        f"Активен: {'да' if user.is_active else 'нет'} · Заблокирован: {'да' if user.is_blocked else 'нет'}",
        "",
    ]

    bars_state = user.bars_state
    bars_creds = user.bars_credentials
    bars_profile = user.bars_profile
    bars_settings = user.bars_settings
    lines.append(f"📗 <b>БАРС</b>: {_STATUS_LABELS.get(getattr(bars_state, 'status', None), '—')}")
    lines.append(f"   Логин: {bars_creds.login if bars_creds and bars_creds.login else '—'}")
    if bars_profile and (bars_profile.fio or bars_profile.group):
        responsible = " · ответственный за журнал" if bars_profile.is_journal_responsible else ""
        lines.append(
            f"   Профиль: {bars_profile.fio or '—'} · {bars_profile.group or '—'}{responsible}"
        )
    else:
        lines.append("   Профиль: — (не загружен)")
    if bars_state and bars_state.last_check_at:
        lines.append(f"   Последняя проверка: {fmt_dt(bars_state.last_check_at)}")
    if bars_state and bars_state.last_error_message:
        lines.append(f"   Последняя ошибка: {bars_state.last_error_message[:120]}")
    lines.append("")

    osep_state = user.osep_state
    osep_creds = user.osep_credentials
    osep_settings = user.osep_settings
    lines.append(f"📧 <b>ОСЭП</b>: {_STATUS_LABELS.get(getattr(osep_state, 'status', None), '—')}")
    lines.append(f"   Логин: {osep_creds.login if osep_creds and osep_creds.login else '—'}")
    if osep_settings and osep_settings.blacklist:
        lines.append(f"   Чёрный список: {len(osep_settings.blacklist)} шт.")
    else:
        lines.append("   Чёрный список: пуст")
    if osep_state and osep_state.last_check_at:
        lines.append(f"   Последняя проверка: {fmt_dt(osep_state.last_check_at)}")
    lines.append("")

    lines.append("⚙️ <b>Настройки БАРС</b> (индивидуальные):")
    if bars_settings is not None:
        for _key, (title, field) in _USER_TOGGLES.items():
            lines.append(f"   {title}: <b>{_yes_no(bool(getattr(bars_settings, field)))}</b>")
    else:
        lines.append("   (используются значения по умолчанию)")
    lines.append("")

    eff = merge_settings(global_row, override_row)
    lines.append("⏱ <b>Опрос</b> (кастомное перекрывает глобальное):")
    for field, title in _OVERRIDE_INT_FIELDS.items():
        personal = getattr(override_row, field, None) if override_row else None
        if personal is not None:
            lines.append(f"   {title}: <b>кастомно ({personal})</b> · глобально ({getattr(eff, field)})")
        else:
            lines.append(f"   {title}: <b>глобально ({getattr(eff, field)})</b>")
    auto_personal = getattr(override_row, "auto_scale_enabled", None) if override_row else None
    if auto_personal is not None:
        lines.append(f"   Авто-масштаб: <b>кастомно ({_yes_no(auto_personal)})</b>")
    else:
        lines.append(f"   Авто-масштаб: <b>глобально ({_yes_no(eff.auto_scale_enabled)})</b>")

    rows = []
    rows.append(
        [
            _btn(
                "🚫 Заблокировать" if not user.is_blocked else "✅ Разблокировать",
                f"adm:ublock:{user_id}",
            ),
            _btn(
                "⚪ Снять активность" if user.is_active else "🟢 Вернуть активность",
                f"adm:uactive:{user_id}",
            ),
        ]
    )
    rows.append([_btn("🔄 Обновить", f"adm:urefresh:{user_id}")])
    rows += [
        [_btn(f"{title}: {_yes_no(bool(getattr(bars_settings, field, False)))} ⚡", f"adm:uset:{user_id}:{_key}")]
        for _key, (title, field) in _USER_TOGGLES.items()
    ]
    rows += [
        [_btn(f"{title} — задать", f"adm:uovset:{user_id}:{field}")]
        for field, title in _OVERRIDE_INT_FIELDS.items()
    ]
    rows.append([_btn("Сбросить кастомные настройки опроса", f"adm:uovclear:{user_id}:all")])
    rows.append([_btn("🔎 Найти другого", "adm:userfind")])
    rows.append([_btn("◀️ Назад", "adm:menu")])
    return "\n".join(lines), _kb(rows)


async def _disable_watchers(uow: UnitOfWork, user_id: int) -> int:
    """Выключить вотчеры пользователя (ACTIVE → INACTIVE). Сколько выключено."""
    off = 0
    for state in (
        await uow.bars_state.get_by_user_id(user_id),
        await uow.osep_state.get_by_user_id(user_id),
    ):
        if state is not None and state.status == WatchStates.ACTIVE:
            state.status = WatchStates.INACTIVE
            off += 1
    return off


async def _show_user(callback: CallbackQuery, user_id: int) -> None:
    card = await _user_card_text(user_id)
    if card is None:
        await callback.answer("Пользователь не найден", show_alert=True)
        return
    text, keyboard = card
    await _safe_edit(callback.message, text, keyboard)


# ──────────────────────────────── логи ───────────────────────────────────────


async def _logs_panel(callback: CallbackQuery) -> None:
    days = list_log_dates()
    if not days:
        await _safe_edit(
            callback.message,
            "📜 <b>Логи</b>\n\nЛогов пока нет.",
            _kb([[_btn("◀️ Назад", "adm:menu")]]),
        )
        return
    rows = [
        [_btn(f"📥 {day.strftime('%d.%m.%Y')}", f"adm:log:{day.strftime('%Y%m%d')}")]
        for day in days
    ]
    rows.append([_btn("◀️ Назад", "adm:menu")])
    await _safe_edit(
        callback.message,
        "📜 <b>Логи</b>\n\n"
        "Выберите дату — придёт лог за этот день.\n"
        "Показаны только те даты, за которые логи ещё есть.",
        _kb(rows),
    )


async def _send_logs_day(callback: CallbackQuery, stamp: str) -> None:
    """Отправить лог за дату (YYYYMMDD); нет лога — сказать об этом."""
    try:
        day = datetime.strptime(stamp, "%Y%m%d").date()
    except ValueError:
        await callback.answer("Неизвестная дата", show_alert=True)
        return
    label = day.strftime("%d.%m.%Y")
    try:
        zips = build_log_zips(day)
    except Exception as error:
        log.error("лог за {}: не получилось собрать архив: {}", label, error)
        await callback.answer("Не получилось собрать архив лога", show_alert=True)
        return
    if not zips:
        # лог мог удалиться (ротация), хотя кнопка осталась в старом сообщении
        await callback.answer(f"📜 Лога за {label} уже не существует", show_alert=True)
        return
    await callback.answer("Отправляю лог…")
    log.info(
        "лог за {}: отправка ({} частей) | tg={}",
        label,
        len(zips),
        callback.from_user.id,
    )
    try:
        for i, zip_path in enumerate(zips):
            caption = f"📜 Лог за {label}"
            if len(zips) > 1:
                caption += f" (часть {i + 1}/{len(zips)})"
            caption += f"\nРазмер: {format_size(zip_path.stat().st_size)}"
            await send_with_retry(
                lambda p=zip_path, c=caption: callback.message.answer_document(
                    FSInputFile(path=p), caption=c
                )
            )
    except Exception as error:
        log.error(
            "лог за {}: ошибка отправки | tg={}: {}",
            label,
            callback.from_user.id,
            error,
        )
        await callback.message.answer(f"❌ Не получилось отправить лог: {error}")
        return
    log.info("лог за {}: отправлен | tg={}", label, callback.from_user.id)


# ──────────────────────────────── роутинг ────────────────────────────────────


@router.message(Command("admin"))
async def cmd_admin(message: Message) -> None:
    if not await _require_admin(message=message):
        return
    await message.answer(_MENU_TEXT, reply_markup=_menu_kb())


@router.message(Command("clearqueue"))
async def cmd_clear_queue(message: Message) -> None:
    """Только для тестов: вычистить очереди событий (bot и worker).

    Ожидающие в Redis события (marks.changed, mail.received и т.п.) удаляются
    и бот их больше не доставит: полезно после тестовых сценариев, когда
    накопленные уведомления уже не нужны.
    """
    if not await _require_admin(message=message):
        return
    from src.events import QUEUE_BOT, QUEUE_WORKER, get_redis

    redis = await get_redis()
    report = []
    for name in (QUEUE_BOT, QUEUE_WORKER):
        pending = await redis.zcard(name)
        await redis.delete(name)
        report.append(f"{name}: удалено ожидающих событий — {pending}")
    log.info("очереди очищены | {}", "; ".join(report))
    await message.answer("🗑 Очереди очищены\n\n" + "\n\n".join(report))


# ──────────────────────── тестовая доступность сервисов ────────────────────────


_SIM_SERVICES = {"bars", "osep"}


@router.message(Command("simup", "simdown"))
async def cmd_sim_toggle(message: Message) -> None:
    """Только для тестов (sim/file): имитировать падение/подъём сервисов.

    Пишет simdata/service.json — worker читает его при каждой проверке, так
    что «сервис упал» действует в обоих процессах и даёт события
    ServiceStatusChanged (уведомления админам при включённом тоггле).
    """
    if settings.integration_mode == "real":
        return  # в real команды не существует
    if not await _require_admin(message=message):
        return
    args = message.text.split()
    cmd = args[0].split("@")[0]
    arg = args[1].lower().split("@")[0] if len(args) > 1 else ""
    if arg not in _SIM_SERVICES | {"all"}:
        await message.answer(
            "Использование:\n\n"
            "<code>/simdown bars|osep|all</code> — сервис недоступен\n\n"
            "<code>/simup bars|osep|all</code> — сервис доступен"
        )
        return
    down = cmd == "/simdown"
    targets = _SIM_SERVICES if arg == "all" else {arg}

    from src.integrations.file_sim import data_dir

    path = data_dir() / "service.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for service in targets:
        data[f"{service}_up"] = not down
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("sim: доступность изменена | {} → {}", ",".join(sorted(targets)), "down" if down else "up")
    states = ", ".join(
        f"{service.upper()}: {'доступен' if data.get(f'{service}_up', True) else 'НЕ доступен'}"
        for service in sorted(_SIM_SERVICES)
    )
    await message.answer(f"Тестовая доступность обновлена ({states})")


@router.callback_query(F.data.startswith("adm:"))
async def admin_callback(callback: CallbackQuery, state: FSMContext) -> None:
    if not await _require_admin(callback=callback):
        return
    parts = callback.data.split(":")
    action = parts[1]

    if action == "menu":
        await state.clear()
        await _safe_edit(callback.message, _MENU_TEXT, _menu_kb())
    elif action == "glob":
        await _globals_panel(callback)
    elif action == "globset":
        field = parts[2]
        if field not in _GLOBAL_INT_FIELDS:
            await callback.answer("Неизвестное поле")
            return
        await state.set_state(AdminStates.edit_global)
        await state.update_data(field=field)
        await callback.message.answer(
            f"Введите значение для «{_GLOBAL_INT_FIELDS[field]}» (целое число):"
        )
    elif action == "globtoggle":
        field = parts[2]
        async with session_factory() as session:
            uow = UnitOfWork(session)
            row = await uow.config.get_or_create_global()
            setattr(row, field, not bool(getattr(row, field)))
            await uow.commit()
        await _globals_panel(callback)
    elif action == "notif":
        await _notif_panel(callback)
    elif action == "notiftoggle":
        field = parts[2]
        async with session_factory() as session:
            uow = UnitOfWork(session)
            row = await uow.config.get_or_create_global()
            setattr(row, field, not bool(getattr(row, field)))
            await uow.commit()
        await _notif_panel(callback)
    elif action == "admins":
        await _admins_panel(callback)
    elif action == "adminadd":
        await state.set_state(AdminStates.add_admin)
        await callback.message.answer("Введите telegram_id нового администратора:")
    elif action == "adminrm":
        ok, text = await remove_admin(int(parts[2]))
        await callback.answer(text, show_alert=not ok)
        await _admins_panel(callback)
    elif action == "users":
        await _users_panel(callback)
    elif action == "userfind":
        await state.set_state(AdminStates.find_user)
        await callback.message.answer("Введите telegram_id или @username:")
    elif action == "user":
        await _show_user(callback, int(parts[2]))
    elif action == "urefresh":
        await _show_user(callback, int(parts[2]))
    elif action == "ublock":
        user_id = int(parts[2])
        async with session_factory() as session:
            uow = UnitOfWork(session)
            user = await uow.users.get_by_id(user_id)
            if user is None:
                await callback.answer("Пользователь не найден", show_alert=True)
                return
            user.is_blocked = not user.is_blocked
            note = ""
            if user.is_blocked:
                off = await _disable_watchers(uow, user_id)
                note = f" · вотчеров выключено: {off}"
            await uow.commit()
            log.info("админ: блокировка {} → {} | user={}", not user.is_blocked, user.is_blocked, user_id)
        await callback.answer(
            "Пользователь заблокирован" + note if user.is_blocked else "Пользователь разблокирован"
        )
        await _show_user(callback, user_id)
    elif action == "uactive":
        user_id = int(parts[2])
        async with session_factory() as session:
            uow = UnitOfWork(session)
            user = await uow.users.get_by_id(user_id)
            if user is None:
                await callback.answer("Пользователь не найден", show_alert=True)
                return
            user.is_active = not user.is_active
            note = ""
            if not user.is_active:
                off = await _disable_watchers(uow, user_id)
                note = f" · вотчеров выключено: {off}"
            await uow.commit()
            log.info("админ: активность {} → {} | user={}", not user.is_active, user.is_active, user_id)
        await callback.answer(
            "Активность снята (рассылка не отправляется)" + note
            if not user.is_active
            else "Пользователь снова активен"
        )
        await _show_user(callback, user_id)
    elif action == "uset":
        user_id, key = int(parts[2]), parts[3]
        _group, _name, field = key.partition(":")
        async with session_factory() as session:
            uow = UnitOfWork(session)
            settings_row = await uow.bars_settings.get_by_user_id(user_id)
            if settings_row is not None:
                setattr(settings_row, field, not bool(getattr(settings_row, field)))
                await uow.commit()
        await _show_user(callback, user_id)
    elif action == "uovset":
        user_id, field = int(parts[2]), parts[3]
        await state.set_state(AdminStates.edit_override)
        await state.update_data(user_id=user_id, field=field)
        await callback.message.answer(
            f"Введите значение для «{_OVERRIDE_INT_FIELDS.get(field, field)}» "
            "или <code>-</code> для сброса к глобальному:"
        )
    elif action == "uovclear":
        user_id = int(parts[2])
        async with session_factory() as session:
            uow = UnitOfWork(session)
            row = await uow.config.get_override(user_id)
            if row is not None:
                for field in _OVERRIDE_INT_FIELDS:
                    setattr(row, field, None)
                row.auto_scale_enabled = None
                await uow.commit()
        await callback.answer("Кастомные настройки опроса сброшены")
        await _show_user(callback, user_id)
    elif action == "logs":
        await _logs_panel(callback)
    elif action == "log":
        await _send_logs_day(callback, parts[2] if len(parts) > 2 else "")
    elif action == "bcast":
        await state.set_state(AdminStates.broadcast)
        await callback.message.answer(
            "📣 <b>Рассылка</b>\n\n"
            "Введите сообщение для пользователей (текст, фото или документ).\n\n"
            "Перед отправкой вы увидите, как оно будет выглядеть у пользователей, "
            "и подтвердите рассылку."
        )
    elif action == "bcastsend":
        data = await state.get_data()
        src_chat = data.get("bcast_chat")
        src_msg = data.get("bcast_msg")
        await state.clear()
        if not src_chat or not src_msg:
            await callback.answer("Нет сообщения для рассылки", show_alert=True)
            return
        await callback.answer("Рассылка началась…")
        async with session_factory() as session:
            uow = UnitOfWork(session)
            users = await uow.users.get_active_users()
            targets = [(u.id, u.telegram_id) for u in users if not u.is_blocked]
        sent = deactivated = failed = 0
        for _db_id, telegram_id in targets:
            try:
                # ≤20 сообщений/с + повтор при 429 (TelegramRetryAfter)
                await send_with_retry(
                    lambda t=telegram_id: callback.message.bot.copy_message(
                        t, src_chat, src_msg
                    )
                )
                sent += 1
            except TelegramForbiddenError:
                # пользователь заблокировал бота — деактивируем, вотчеры выключаются
                await deactivate_user(telegram_id)
                deactivated += 1
            except Exception as error:
                failed += 1
                log.error("рассылка: ошибка отправки | user={}: {}", telegram_id, error)
        log.info(
            "рассылка завершена | получателей: {} | доставлено: {} | заблокировали бота: {} | ошибок: {}",
            len(targets),
            sent,
            deactivated,
            failed,
        )
        await callback.message.answer(
            "📣 <b>Рассылка завершена</b>\n\n"
            f"Получателей: {len(targets)}\n\n"
            f"Доставлено: {sent}\n\n"
            f"Заблокировали бота (деактивированы): {deactivated}\n\n"
            f"Ошибок: {failed}"
        )
    elif action == "bcastcancel":
        await state.clear()
        await callback.answer("Рассылка отменена")
        await callback.message.edit_text("Рассылка отменена")
    else:
        await callback.answer()


# ────────────────────────────── FSM-вводы ────────────────────────────────────


@router.message(AdminStates.edit_global)
async def admin_edit_global(message: Message, state: FSMContext) -> None:
    if not await _require_admin(message=message):
        return
    data = await state.get_data()
    field = data.get("field")
    try:
        value = int(message.text.strip())
        if value <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Нужно целое положительное число. Попробуйте ещё раз или /start.")
        return
    async with session_factory() as session:
        uow = UnitOfWork(session)
        row = await uow.config.get_or_create_global()
        setattr(row, field, value)
        await uow.commit()
    await state.clear()
    await message.answer(
        f"✅ {_GLOBAL_INT_FIELDS.get(field, field)} = {value}",
        reply_markup=_kb([[_btn("◀️ В админку", "adm:menu")]]),
    )


@router.message(AdminStates.find_user)
async def admin_find_user(message: Message, state: FSMContext) -> None:
    if not await _require_admin(message=message):
        return
    query = message.text.strip().lstrip("@")
    user_id: int | None = None
    if query.isdigit():
        async with session_factory() as session:
            uow = UnitOfWork(session)
            user = await uow.users.get_by_telegram_id(int(query))
        user_id = user.id if user else None
    else:
        from sqlalchemy import select

        from src.db.models import User

        async with session_factory() as session:
            user = (
                await session.execute(select(User).where(User.username == query))
            ).scalars().first()
            user_id = user.id if user else None
    await state.clear()
    if user_id is None:
        await message.answer(f"Пользователь «{message.text.strip()}» не найден.")
        return
    card = await _user_card_text(user_id)
    if card is None:
        await message.answer("Пользователь не найден.")
        return
    text, keyboard = card
    await message.answer(text, reply_markup=keyboard)


@router.message(AdminStates.add_admin)
async def admin_add_admin(message: Message, state: FSMContext) -> None:
    if not await _require_admin(message=message):
        return
    raw = message.text.strip()
    if not raw.lstrip("-").isdigit():
        await message.answer("Нужен telegram_id (число).")
        return
    await state.clear()
    ok, text = await add_admin(int(raw), message.from_user.username)
    await message.answer(("✅ " if ok else "⚠️ ") + text)


@router.message(AdminStates.edit_override)
async def admin_edit_override(message: Message, state: FSMContext) -> None:
    if not await _require_admin(message=message):
        return
    data = await state.get_data()
    user_id, field = data.get("user_id"), data.get("field")
    raw = message.text.strip()
    if raw in {"-", "—", "сброс"}:
        value = None
    else:
        try:
            value = int(raw)
            if value <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Введите целое положительное число или <code>-</code> для сброса.")
            return
    async with session_factory() as session:
        uow = UnitOfWork(session)
        row = await uow.config.get_override(user_id)
        if row is None:
            from src.db.models import UserOverride

            row = UserOverride(user_id=user_id)
            uow.session.add(row)
        setattr(row, field, value)
        await uow.commit()
    await state.clear()
    label = "глобальному значению" if value is None else str(value)
    await message.answer(f"✅ Установлено: {_OVERRIDE_INT_FIELDS.get(field, field)} → {label}")
    card = await _user_card_text(user_id)
    if card:
        text, keyboard = card
        await message.answer(text, reply_markup=keyboard)


@router.message(AdminStates.broadcast)
async def admin_broadcast_input(message: Message, state: FSMContext) -> None:
    if not await _require_admin(message=message):
        return
    # запоминаем исходное сообщение (для копирования пользователям) и показываем
    # предпросмотр — как оно будет выглядеть у пользователей
    await state.update_data(bcast_chat=message.chat.id, bcast_msg=message.message_id)
    await message.copy_to(message.chat.id)
    await message.answer(
        "Так сообщение увидят пользователи ↑\n\n"
        "Получатели: активные пользователи (is_active и не заблокированы).\n\n"
        "Подтвердить рассылку?",
        reply_markup=_kb(
            [
                [_btn("✅ Отправить всем активным", "adm:bcastsend")],
                [_btn("❌ Отмена", "adm:bcastcancel")],
            ]
        ),
    )
