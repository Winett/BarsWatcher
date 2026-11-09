"""Хендлеры Telegram-бота: меню, панели сервисов, настройки, вход и 2FA.

Логика панелей — по BarsWatcherBotLogic.drawio. Ввод кода 2FA начинается
только по кнопке «Ввести код 2FA» (пользователь сам решает, когда).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from loguru import logger
from sqlalchemy import func, select

from src.core.config import settings
from src.db.database import session_factory
from src.db.models import (
    BarsCredentials,
    BarsSettings,
    BarsState,
    OsepCredentials,
    OsepSettings,
    OsepState,
    ServiceKind,
    User,
    WatchStates,
)
from src.events import get_redis, publish
from src.events.contracts import CheckRequested, LessonsCheckRequested
from src.integrations import (
    AuthError,
    AuthResult,
    AuthStatus,
    ServiceUnavailableError,
    get_bars_client,
    get_osep_client,
)
from src.repositories.uow import UnitOfWork
from src.services import save_bars_profile
from src.services.admins import admin_telegram_ids

from . import keyboards as kb
from . import panels
from .callbacks import ack, answers

__all__ = ["build_router"]

router = Router()

CODE_TTL = timedelta(minutes=5)  # сколько живёт ввод кода 2FA
AUTH_TIMEOUT = 90.0  # потолок на авторизацию с ретраями внутри клиента
_SERVICES = {"bars": ServiceKind.BARS, "osep": ServiceKind.OSEP}

class LoginFlow(StatesGroup):
    waiting_login = State()
    waiting_password = State()


class TwoFAFlow(StatesGroup):
    waiting_code = State()


class BlacklistFlow(StatesGroup):
    waiting_pattern = State()


def _client(service: str, user_id: int):
    # клиент один на пользователя (кэш в src.integrations): 2FA-флоу требует
    # одного и того же клиента между сообщениями; сессии переживают в БД
    if service == "bars":
        return get_bars_client(user_id)
    return get_osep_client(user_id)


async def _publish_check(db_id: int, service: str) -> None:
    """Попросить worker проверить сервис (best-effort: очередь не должна ронять UI)."""
    try:
        await publish(CheckRequested(user_id=db_id, service=_SERVICES[service]))
    except Exception as error:
        logger.error("не удалось запустить проверку (очередь недоступна?): {}", error)


async def _ensure_user(uow, telegram_id: int, username: str | None = None) -> int:
    """Пользователь по telegram_id → внутренний User.id (создаёт при первом визите)."""
    user = await uow.users.get_by_telegram_id(telegram_id)
    if user is None:
        user = User(telegram_id=telegram_id, username=username)
        await uow.users.add(user)
        await uow.commit()
    return user.id


async def _load_panel_data(service: str, telegram_id: int) -> dict:
    async with session_factory() as session:
        uow = UnitOfWork(session)
        db_id = await _ensure_user(uow, telegram_id)
        user = await uow.users.get_by_id(
            db_id,
            with_bars_state=True,
            with_bars_credentials=True,
            with_bars_profile=True,
            with_osep_state=True,
            with_osep_credentials=True,
        )
        if service == "bars":
            state = user.bars_state
            creds = user.bars_credentials
        else:
            state = user.osep_state
            creds = user.osep_credentials
        login = creds.login if creds else None
        watching = panels.is_watching(state.status if state else None)
        last_check = state.last_check_at if state else None
        profile = user.bars_profile
        responsible = bool(profile and profile.is_journal_responsible)

    # доступность из Redis-трекера — без прямого HTTP-запроса к сервису
    kind = ServiceKind(service) if isinstance(service, str) else service
    available, available_since = await _service_status(kind)
    return {
        "watching": watching,
        "status": state.status if state else None,
        "available": available,
        "available_since": available_since,
        "last_check": last_check,
        "login": login,
        "responsible": responsible,
    }


async def _render_panel(message: Message, service: str, telegram_id: int) -> None:
    data = await _load_panel_data(service, telegram_id)
    text = panels.service_panel_text(
        service,
        watching=data["watching"],
        status=data["status"],
        available=data["available"],
        available_since=data["available_since"],
        last_check=data["last_check"],
        login=data["login"],
    )
    keyboard = kb.service_panel(
        service, watching=data["watching"], available=data["available"]
    )
    await _safe_edit(message, text, keyboard)


# ─────────────────────────────── /start и меню ───────────────────────────────


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    # /start сбрасывает любое состояние (ввод настроек, рассылка и т.п.)
    await state.clear()
    async with session_factory() as session:
        uow = UnitOfWork(session)
        existing = await uow.users.get_by_telegram_id(message.from_user.id)
        await _ensure_user(uow, message.from_user.id, message.from_user.username)
    if existing is None:
        await _notify_new_user(message)
    await message.answer(panels.WELCOME, reply_markup=kb.main_menu())


async def _notify_new_user(message: Message) -> None:
    """Уведомить админов о новом пользователе (если включено в админке)."""
    try:
        async with session_factory() as session:
            uow = UnitOfWork(session)
            row = await uow.config.get_global()
        if row is None or not row.notify_new_users:
            return
        user = message.from_user
        # <code> — username моноширинный и копируется тапом (parse_mode=HTML)
        name = f"<code>@{user.username}</code>" if user.username else user.full_name
        parts = [f"🆕 Новый пользователь: {name}"]
        # <code> — id моноширинный и копируется тапом (parse_mode=HTML у бота)
        parts.append(f"telegram_id: <code>{user.id}</code>")
        if user.first_name:
            parts.append(f"first_name: {user.first_name}")
        if user.last_name:
            parts.append(f"last_name: {user.last_name}")
        from src.services.admins import notify_admins

        await notify_admins(message.bot, "\n".join(parts))
    except Exception as error:
        logger.error("не удалось уведомить админов о новом пользователе: {}", error)


@router.callback_query(F.data == "menu:home")
@answers
async def menu_home(callback: CallbackQuery) -> None:
    await _safe_edit(callback.message, panels.WELCOME, kb.main_menu())
    await callback.answer()


@router.callback_query(F.data == "menu:refresh")
@answers
async def menu_refresh(callback: CallbackQuery) -> None:
    """«Обновить» на главной: перерисовать текущее сообщение (без запросов)."""
    await _safe_edit(callback.message, panels.WELCOME, kb.main_menu())
    await callback.answer("Обновлено")


@router.callback_query(F.data.startswith("menu:"))
@answers
async def menu_service(callback: CallbackQuery) -> None:
    service = callback.data.split(":")[1]
    if service not in _SERVICES:
        await callback.answer()
        return
    await callback.answer()  # снимаем «часики» сразу — панель рисуем ниже
    await _render_panel(callback.message, service, callback.from_user.id)


# ─────────────────────────────── панель сервиса ───────────────────────────────


@router.callback_query(F.data.startswith("panel:"))
@answers
async def panel_action(callback: CallbackQuery, state: FSMContext) -> None:
    _, service, action = callback.data.split(":")
    user_id = callback.from_user.id

    if action == "na":
        await callback.answer("Сервис сейчас недоступен", show_alert=True)
        return

    if action == "refresh":
        # «Обновить» только перерисовывает сообщение — моментальные запросы
        # в её задачу не входят (проверки идут по расписанию планировщика)
        await callback.answer("Обновлено")
        await _render_panel(callback.message, service, user_id)
        return

    if action == "disable":
        await _set_watching(service, user_id, watching=False)
        await callback.answer("Отслеживание выключено")
        await _render_panel(callback.message, service, user_id)
        return

    if action == "enable":
        await _start_auth(callback, state, service, user_id)
        return

    if action == "creds":
        await state.update_data(service=service)
        await state.set_state(LoginFlow.waiting_login)
        await _safe_edit(
            callback.message,
            f"Введите логин для {panels.service_title(service)}:",
            kb.back_kb(f"menu:{service}"),
        )
        await callback.answer()
        return

    if action == "settings":
        await _render_settings(callback, service, user_id)
        return


async def _authenticate_client(service: str, client, login: str, password: str):
    """Авторизация с потолком времени; ошибка сервиса — не «неверные креды».

    Возвращает AuthResult. При таймауте/сбое клиента — SERVICE_ERROR с явным
    текстом: это НЕ «неверный логин/пароль» и НЕ успех (вотчер не включаем,
    но и не помечаем креды неверными).
    """
    try:
        return await asyncio.wait_for(
            client.authenticate(login, password), timeout=AUTH_TIMEOUT
        )
    except TimeoutError:
        logger.error("авторизация {} превысила таймаут ({}с)", service, AUTH_TIMEOUT)
        return AuthResult(
            AuthStatus.SERVICE_ERROR,
            error="Сервис не ответил за отведённое время — попробуйте позже",
        )
    except Exception as error:  # noqa: BLE001 — UI не должно падать на ошибке клиента
        logger.error("авторизация {} не удалась (ошибка клиента): {}", service, error)
        return AuthResult(
            AuthStatus.SERVICE_ERROR,
            error="Сервис временно недоступен — попробуйте позже",
        )


async def _start_auth(
    callback: CallbackQuery, state: FSMContext, service: str, user_id: int
) -> None:
    """Включить отслеживание: авторизация сохранёнными кредами или ввод новых."""
    # проверяем доступность сервиса ДО попытки авторизации (без прямого запроса)
    kind = ServiceKind(service) if isinstance(service, str) else service
    up, _ = await _service_status(kind)
    if not up:
        title = panels.service_title(service)
        await callback.answer(
            f"⚠️ {title} временно недоступен, попробуйте позже",
            show_alert=True,
        )
        return

    async with session_factory() as session:
        uow = UnitOfWork(session)
        db_id = await _db_user_id(user_id, uow)
        creds = await _creds(uow, service, db_id)
        login, password = (creds.login, creds.password) if creds else (None, None)

    if not login or not password:
        await state.update_data(service=service)
        await state.set_state(LoginFlow.waiting_login)
        await _safe_edit(
            callback.message,
            f"Введите логин для {panels.service_title(service)}:",
            kb.back_kb(f"menu:{service}"),
        )
        await callback.answer()
        return

    # снять «часики» с кнопки до долгой авторизации (HTTP)
    await ack(callback)
    await _safe_edit(callback.message, "Попытка авторизоваться…")
    client = _client(service, db_id)
    await _restore_session(service, db_id, client)

    # сначала пробуем живые куки: обычный запрос (БАРС — профиль, ОСЭП — папки).
    # Если сессия жива — авторизация не нужна; если мертва — идём авторизоваться.
    alive = False
    try:
        if service == "bars":
            await client.fetch_profile()
        else:
            await client.fetch_folder_counts()
        alive = True
    except Exception:
        alive = False  # сессия слетела или сервис сбоит — авторизуемся заново

    if alive:
        result = AuthResult(AuthStatus.OK)
    else:
        result = await _authenticate_client(service, client, login, password)
    await _save_session(service, db_id, client)
    await _after_auth(
        callback.message, state, service, user_id, db_id, result, client, callback
    )


async def _after_auth(
    message: Message,
    state: FSMContext,
    service: str,
    telegram_id: int,
    db_id: int,
    result,
    client,
    callback: CallbackQuery | None = None,
) -> None:
    if result.status is AuthStatus.OK:
        await _set_watching(service, telegram_id, watching=True)
        await _publish_check(db_id, service)
        await _safe_edit(message, "Авторизация успешна\n\nОтслеживание включено")
        await _render_panel(message, service, telegram_id)
        # включение вотчера = момент перепроверки профиля (ФИО/группа/ответственный
        # могли измениться) — как и после запуска бота/worker'а; после ответа UI,
        # чтобы не задерживать реакцию на кнопку
        await _refresh_profile(service, db_id, client)
        return

    if result.status is AuthStatus.TWO_FACTOR_REQUIRED:
        await _set_watching(service, telegram_id, waiting_2fa=True)
        # код не отправляется сам по себе — отправляем сразу (как в master)
        destination = None
        try:
            send_result = await client.send_2fa_code()
            destination = send_result.destination
        except AuthError:
            pass  # состояние истекло — поможет «Отправить код повторно»
        except Exception as error:
            logger.error("не удалось отправить код 2FA: {}", error)
        await _save_session(service, db_id, client)
        where = destination or result.error or "указанное в БАРС"
        await state.update_data(code_destination=where)
        await _safe_edit(
            message,
            f"🔐 Требуется ввести 2FA код.\n"
            f"Код отправлен вам на: {where}\n"
            "Нажмите «Ввести код 2FA», когда будете готовы.",
            kb.two_fa_prompt_kb(service),
        )
        return

    if result.status is AuthStatus.SERVICE_ERROR:
        # сервис не ответил — это НЕ «неверный логин/пароль» и НЕ успех:
        # вотчер не включаем, креды не помечаем неверными
        reason = result.error or "Сервис временно недоступен — попробуйте позже"
        await _safe_edit(message, f"⚠️ {reason}", kb.back_kb(f"menu:{service}"))
        return

    # сервер сам отклонил логин/пароль — единственный случай «неверные креды»
    reason = result.error or "Неверно указан логин или пароль"
    await _safe_edit(message, f"⚠️ {reason}", kb.back_kb(f"menu:{service}"))


# ─────────────────────────────── ввод логина/пароля ───────────────────────────────


@router.message(LoginFlow.waiting_login, F.text)
async def on_login(message: Message, state: FSMContext) -> None:
    login = message.text.strip()
    if not login:
        await message.answer("Логин не может быть пустым, попробуйте ещё раз:")
        return
    await state.update_data(login=login)
    await state.set_state(LoginFlow.waiting_password)
    await message.answer("Введите пароль:")


@router.message(LoginFlow.waiting_password, F.text)
async def on_password(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    service = data.get("service")
    login = data.get("login")
    password = message.text
    await message.delete()  # пароль не оставляем в чате

    db_id = await _db_user_id(message.from_user.id)
    await _save_creds(service, db_id, login, password)
    await state.clear()

    status_message = await message.answer("Попытка авторизоваться…")
    client = _client(service, db_id)
    result = await _authenticate_client(service, client, login, password)
    await _save_session(service, db_id, client)
    await _after_auth(
        status_message, state, service, message.from_user.id, db_id, result, client
    )


# ─────────────────────────────── 2FA ───────────────────────────────


@router.callback_query(F.data.startswith("2fa:"))
@answers
async def two_fa_action(callback: CallbackQuery, state: FSMContext) -> None:
    _, service, action = callback.data.split(":")
    db_id = await _db_user_id(callback.from_user.id)

    if action == "input":
        # снять «часики» до HTTP-отправки кода
        await ack(callback)
        client = _client(service, db_id)
        # ожидающее 2FA-состояние могло быть создано worker'ом — восстанавливаем
        await _restore_session(service, db_id, client)
        # код мог не отправиться (сессия слетела) — отправляем при вводе,
        # иначе пользователь вводит код, которого нет
        destination = None
        try:
            send_result = await client.send_2fa_code()
            destination = send_result.destination
        except AuthError:
            pass  # состояние истекло — поможет «Отправить код снова»
        except Exception as error:
            logger.error("не удалось отправить код 2FA при вводе: {}", error)
        await _save_session(service, db_id, client)

        await state.set_state(TwoFAFlow.waiting_code)
        where = destination or None
        if where:
            await state.update_data(
                service=service,
                code_requested_at=datetime.now(UTC).isoformat(),
                code_destination=where,
            )
            hint = f" (код отправлен: {where})"
        else:
            await state.update_data(
                service=service, code_requested_at=datetime.now(UTC).isoformat()
            )
            hint = ""
        await _safe_edit(
            callback.message,
            f"Введите код 2FA{hint}:",
            kb.two_fa_input_kb(service),
        )
        return

    if action == "resend":
        client = _client(service, db_id)
        # ожидающее 2FA-состояние могло быть создано worker'ом — восстанавливаем
        await _restore_session(service, db_id, client)
        try:
            result = await client.send_2fa_code()
        except AuthError:
            await callback.answer("Сессия устарела — начните сначала", show_alert=True)
            await state.clear()
            await _render_panel(callback.message, service, callback.from_user.id)
            return
        except Exception as error:
            logger.error("не удалось отправить код 2FA повторно: {}", error)
            await callback.answer(
                "Сервис не ответил — попробуйте позже", show_alert=True
            )
            return
        await state.update_data(
            code_requested_at=datetime.now(UTC).isoformat(),
            code_destination=result.destination or "",
        )
        destination = result.destination or "куда — неизвестно"
        await _safe_edit(
            callback.message,
            f"Введите код 2FA, он был отправлен вам повторно ({destination}):",
            kb.two_fa_input_kb(service),
        )
        await callback.answer()
        return

    if action == "cancel":
        await state.clear()
        await callback.answer("Отменено")
        await _render_panel(callback.message, service, callback.from_user.id)


@router.message(TwoFAFlow.waiting_code, F.text)
async def on_2fa_code(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    service = data.get("service")
    requested_at = data.get("code_requested_at")
    code = message.text.strip()

    if requested_at:
        requested = datetime.fromisoformat(requested_at)
        if datetime.now(UTC) - requested > CODE_TTL:
            await state.clear()
            await message.answer(
                "Время ожидания истекло. Нажмите «Ввести код 2FA» ещё раз.",
                reply_markup=kb.two_fa_prompt_kb(service),
            )
            return

    db_id = await _db_user_id(message.from_user.id)
    client = _client(service, db_id)
    # ожидающее 2FA-состояние (токен/аккаунт) могло быть создано worker'ом и
    # храниться в БД — восстанавливаем, иначе verify кидает AuthError
    await _restore_session(service, db_id, client)
    try:
        result = await client.verify_2fa_code(code)
    except (ServiceUnavailableError, ConnectionError, TimeoutError) as error:
        # ответ не получен — это НЕ «код неверный»: оставляем ввод, можно повторить
        logger.warning("проверка 2FA не удалась (сервис не ответил): {}", error)
        await message.answer(
            "⚠️ Сервис не ответил, код не проверен. Попробуйте ввести его ещё раз:",
            reply_markup=kb.two_fa_input_kb(service),
        )
        return
    except AuthError:
        await state.clear()
        await message.answer(
            "Сессия ввода кода устарела. Нажмите «Ввести код 2FA» ещё раз.",
            reply_markup=kb.two_fa_prompt_kb(service),
        )
        return
    await _save_session(service, db_id, client)
    if result.status is AuthStatus.OK:
        await state.clear()
        await _set_watching(service, message.from_user.id, watching=True)
        await _publish_check(db_id, service)
        await message.answer("✅ Код принят — отслеживание включено")
        # включение вотчера = перепроверка профиля (ФИО/группа/ответственный);
        # после ответа пользователю, чтобы не задерживать реакцию
        await _refresh_profile(service, db_id, client)
    else:
        await message.answer(
            "❌ Код введён неверно. Попробуйте снова:",
            reply_markup=kb.two_fa_input_kb(service),
        )


# ─────────────────────────────── настройки ───────────────────────────────


async def _render_settings(callback: CallbackQuery, service: str, user_id: int) -> None:
    db_id = await _db_user_id(user_id)
    if service == "bars":
        async with session_factory() as session:
            uow = UnitOfWork(session)
            obj = await uow.bars_settings.get_by_user_id(db_id)
            hidden = obj.hidden_mark if obj else False
            absences = obj.notify_absences if obj else True
            reminders = obj.reminders_enabled if obj else False
            reminder_time = obj.reminder_time if obj else None
            profile = await uow.bars_profile.get_by_user_id(db_id)
            responsible = bool(profile and profile.is_journal_responsible)
        await _safe_edit(
            callback.message,
            panels.bars_settings_text(
                hidden,
                absences,
                reminders,
                is_journal_responsible=responsible,
                reminder_time=reminder_time,
            ),
            kb.bars_settings_kb(
                hidden_mark=hidden,
                notify_absences=absences,
                reminders=reminders,
                is_journal_responsible=responsible,
            ),
        )
    else:
        async with session_factory() as session:
            uow = UnitOfWork(session)
            obj = await uow.osep_settings.get_by_user_id(db_id)
            blacklist = obj.blacklist if obj else []
        await _safe_edit(
            callback.message,
            panels.osep_settings_text(blacklist),
            kb.osep_settings_kb(),
        )
    await callback.answer()


@router.callback_query(F.data == "bars:pairs")
@answers
async def bars_pairs_check(callback: CallbackQuery) -> None:
    """Проверка пар за сегодня — та же логика, что у напоминания в 18:00."""
    db_id = await _db_user_id(callback.from_user.id)
    await publish(LessonsCheckRequested(user_id=db_id, days=1))
    await callback.answer("Проверяю пары за сегодня…")
    await _safe_edit(
        callback.message,
        "📋 Проверяю пары за сегодня (журнал + расписание)…\n\n"
        "Результат придёт отдельным сообщением.",
        kb.back_kb("menu:bars"),
    )


@router.callback_query(F.data == "bars:lessons")
@answers
async def bars_lessons_check(callback: CallbackQuery) -> None:
    """Сверка пар журнала с расписанием с начала семестра (для ответственного)."""
    # rate limit: не чаще раза в минуту (дорогая операция)
    redis = await get_redis()
    lock_key = f"lessons_lock:{callback.from_user.id}"
    if not await redis.set(lock_key, "1", nx=True, ex=60):
        await callback.answer("⏳ Сверка уже запущена, подождите минуту", show_alert=True)
        return
    db_id = await _db_user_id(callback.from_user.id)
    await publish(LessonsCheckRequested(user_id=db_id, days=0))
    await callback.answer("Сверяю пары с начала семестра…")
    await _safe_edit(
        callback.message,
        "🔍 Сверяю пары журнала с расписанием (с начала семестра)…\n\n"
        "Выведу только непроставленные пары. Результат придёт отдельным сообщением.",
        kb.back_kb("menu:bars"),
    )


@router.callback_query(F.data.startswith("set:bars:"))
@answers
async def bars_setting_toggle(callback: CallbackQuery) -> None:
    field = callback.data.split(":")[2]
    db_id = await _db_user_id(callback.from_user.id)
    async with session_factory() as session:
        uow = UnitOfWork(session)
        obj = await uow.bars_settings.get_by_user_id(db_id)
        if obj is None:
            obj = BarsSettings(user_id=db_id)
            await uow.bars_settings.add(obj)
        if field == "hidden":
            obj.hidden_mark = not obj.hidden_mark
        elif field == "absences":
            obj.notify_absences = not obj.notify_absences
        elif field == "reminders":
            obj.reminders_enabled = not obj.reminders_enabled
        await uow.commit()
    await _render_settings(callback, "bars", callback.from_user.id)


@router.callback_query(F.data.startswith("set:osep:"))
@answers
async def osep_setting_action(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.split(":")[2]
    if action == "bl_add":
        await state.set_state(BlacklistFlow.waiting_pattern)
        await _safe_edit(
            callback.message,
            "Отправьте шаблон отправителя (поддерживается *), "
            "например: *@spam.ru",
            kb.back_kb("menu:osep"),
        )
    elif action == "bl_show":
        db_id = await _db_user_id(callback.from_user.id)
        async with session_factory() as session:
            uow = UnitOfWork(session)
            obj = await uow.osep_settings.get_by_user_id(db_id)
            blacklist = obj.blacklist if obj else []
        await _safe_edit(
            callback.message,
            panels.osep_settings_text(blacklist),
            kb.osep_settings_kb(),
        )
    await callback.answer()


@router.message(BlacklistFlow.waiting_pattern, F.text)
async def on_blacklist_pattern(message: Message, state: FSMContext) -> None:
    pattern = message.text.strip()
    db_id = await _db_user_id(message.from_user.id)
    async with session_factory() as session:
        uow = UnitOfWork(session)
        obj = await uow.osep_settings.get_by_user_id(db_id)
        if obj is None:
            obj = OsepSettings(user_id=db_id)
            await uow.osep_settings.add(obj)
        obj.blacklist = list(obj.blacklist or []) + [pattern]
        await uow.commit()
    await state.clear()
    await message.answer(
        f"Шаблон «{pattern}» добавлен в чёрный список.",
        reply_markup=kb.back_kb("menu:osep"),
    )


# ─────────────────────────────── команды ───────────────────────────────


@router.message(Command("services"))
async def cmd_services(message: Message) -> None:
    bars = await _service_status(ServiceKind.BARS)
    osep = await _service_status(ServiceKind.OSEP)
    await message.answer(panels.services_text(bars, osep))


@router.message(Command("statistic"))
async def cmd_statistic(message: Message) -> None:
    if message.from_user.id not in settings.admins:
        await message.answer("Команда доступна только администраторам.")
        return
    async with session_factory() as session:
        total = (
            await session.execute(select(func.count()).select_from(User))
        ).scalar() or 0
        with_bars = (await session.execute(
            select(func.count()).select_from(BarsCredentials).where(
                BarsCredentials.login.is_not(None)
            )
        )).scalar() or 0
        with_osep = (await session.execute(
            select(func.count()).select_from(OsepCredentials).where(
                OsepCredentials.login.is_not(None)
            )
        )).scalar() or 0
        # «используют» = отслеживание реально включено (статус ACTIVE)
        active_bars = (await session.execute(
            select(func.count()).select_from(BarsState).where(
                BarsState.status == WatchStates.ACTIVE
            )
        )).scalar() or 0
        active_osep = (await session.execute(
            select(func.count()).select_from(OsepState).where(
                OsepState.status == WatchStates.ACTIVE
            )
        )).scalar() or 0
    await message.answer(
        panels.statistic_text(total, with_bars, with_osep, active_bars, active_osep)
    )


# ─────────────────────────────── помощники ───────────────────────────────


async def _safe_edit(
    message: Message, text: str, keyboard=None
) -> None:
    """edit_text без падения на «нечего обновлять».

    «message is not modified» — контент не изменился (двойной тап, повторное
    открытие); «message to edit not found» — сообщение удалено. Оба случая —
    не ошибки и не должны ронять хендлер.
    """
    from aiogram.exceptions import TelegramBadRequest

    try:
        await message.edit_text(text, reply_markup=keyboard)
    except TelegramBadRequest as exc:
        error = str(exc)
        if (
            "message is not modified" in error
            or "message to edit not found" in error
        ):
            return
        raise


async def _db_user_id(telegram_id: int, uow=None) -> int:
    if uow is None:
        async with session_factory() as session:
            uow = UnitOfWork(session)
            user = await uow.users.get_by_telegram_id(telegram_id)
            return user.id if user else telegram_id
    user = await uow.users.get_by_telegram_id(telegram_id)
    return user.id if user else telegram_id


async def _refresh_profile(service: str, db_id: int, client) -> None:
    """Перепроверить профиль БАРС (best-effort) — данные могли измениться.

    Вызывается при включении вотчера пользователем; после запуска процесса
    профиль перепроверяет worker при первой удачной авторизации
    (см. src.services.profiles.profile_stale).
    """
    if service != "bars":
        return  # у ОСЭП профиля нет
    try:
        profile = await client.fetch_profile()
    except Exception as error:
        logger.warning("профиль БАРС не перепроверен: {}", error)
        return
    try:
        async with session_factory() as session:
            uow = UnitOfWork(session)
            await save_bars_profile(uow, db_id, profile)
            await uow.commit()
    except Exception as error:
        logger.warning("профиль БАРС не сохранён: {}", error)


async def _creds(uow, service: str, db_id: int):
    if service == "bars":
        return await uow.bars_credentials.get_by_user_id(db_id)
    return await uow.osep_credentials.get_by_user_id(db_id)


async def _save_creds(service: str, db_id: int, login: str, password: str) -> None:
    async with session_factory() as session:
        uow = UnitOfWork(session)
        if service == "bars":
            obj = await uow.bars_credentials.get_by_user_id(db_id)
            if obj is None:
                obj = BarsCredentials(user_id=db_id)
                await uow.bars_credentials.add(obj)
        else:
            obj = await uow.osep_credentials.get_by_user_id(db_id)
            if obj is None:
                obj = OsepCredentials(user_id=db_id)
                await uow.osep_credentials.add(obj)
        obj.login = login
        obj.password = password
        await uow.commit()


async def _set_watching(
    service: str,
    telegram_id: int,
    *,
    watching: bool = False,
    waiting_2fa: bool = False,
) -> None:
    async with session_factory() as session:
        uow = UnitOfWork(session)
        db_id = await _db_user_id(telegram_id, uow)
        if service == "bars":
            obj = await uow.bars_state.get_by_user_id(db_id)
            if obj is None:
                obj = BarsState(user_id=db_id)
                await uow.bars_state.add(obj)
        else:
            obj = await uow.osep_state.get_by_user_id(db_id)
            if obj is None:
                obj = OsepState(user_id=db_id)
                await uow.osep_state.add(obj)
        old_status = obj.status
        if waiting_2fa:
            obj.status = WatchStates.WAITING_2FA
        elif watching:
            obj.status = WatchStates.ACTIVE
            # ближайшая проверка идёт сразу по CheckRequested (выше), поэтому
            # планировщику ставим обычный интервал — чтобы не дублировать её
            obj.next_check = datetime.now(UTC) + timedelta(seconds=60)
            if service == "osep":
                # включение ОСЭП = новая база: счётчики папок запоминаются без
                # уведомлений о «новых» письмах, копившихся в папках раньше
                obj.folder_counts = None
        else:
            obj.status = WatchStates.INACTIVE
        if old_status != obj.status:
            # переход статуса в лог: видно, кто и когда его поменял (п.3)
            logger.info(
                "статус {} изменён: {} → {} | tg={}",
                service,
                getattr(old_status, "value", old_status) or "—",
                obj.status.value,
                telegram_id,
            )
        await uow.commit()


async def _save_session(service: str, db_id: int, client) -> None:
    """Сохранить сессию клиента (cookies + pending 2FA) в БД — worker её restore'ит."""
    payload = client.export_session()
    async with session_factory() as session:
        uow = UnitOfWork(session)
        if service == "bars":
            obj = await uow.bars_state.get_by_user_id(db_id)
            if obj is None:
                obj = BarsState(user_id=db_id)
                await uow.bars_state.add(obj)
        else:
            obj = await uow.osep_state.get_by_user_id(db_id)
            if obj is None:
                obj = OsepState(user_id=db_id)
                await uow.osep_state.add(obj)
        obj.cookies = payload.get("cookies")
        if hasattr(obj, "pending_2fa"):  # 2FA только у БАРС
            obj.pending_2fa = payload.get("pending_2fa")
        await uow.commit()


async def _restore_session(service: str, db_id: int, client) -> None:
    async with session_factory() as session:
        uow = UnitOfWork(session)
        if service == "bars":
            obj = await uow.bars_state.get_by_user_id(db_id)
        else:
            obj = await uow.osep_state.get_by_user_id(db_id)
        cookies = obj.cookies if obj else None
        pending = getattr(obj, "pending_2fa", None) if obj else None
    if cookies or pending:
        client.restore_session({"cookies": cookies, "pending_2fa": pending})


async def _service_status(service: ServiceKind) -> tuple[bool, datetime | None]:
    """Актуальный статус сервиса: источник истины — tracker в Redis.

    Redis недоступен — не роняем UI: считаем сервис доступным (не пугаем).
    """
    from src.services.availability import AvailabilityTracker

    try:
        redis = await get_redis()
        up, since = await AvailabilityTracker(redis).get_status(service.value)
    except Exception as error:
        logger.warning("статус {} недоступен (Redis?): {}", service.value, error)
        return True, None
    if up is None:
        return True, None  # ещё не проверялся — считаем доступным, не пугаем
    return up, since


# ────────────────────────── предложения и жалобы ──────────────────────────


class FeedbackFlow(StatesGroup):
    suggestion = State()
    report = State()


async def _forward_feedback(message: Message, kind: str) -> bool:
    """Скопировать сообщение пользователя админам."""
    ids = await admin_telegram_ids()
    if not ids:
        return False
    user = message.from_user
    # <code> — username копируется тапом (parse_mode=HTML)
    name = f"<code>@{user.username}</code>" if user.username else user.full_name
    for admin_id in ids:
        try:
            await message.bot.send_message(
                admin_id, f"{kind} от {name} (id=<code>{user.id}</code>):"
            )
            await message.copy_to(admin_id)
        except Exception as error:
            logger.error("не удалось переслать {} админу {}: {}", kind.lower(), admin_id, error)
    return True


@router.message(Command("suggestion"))
async def cmd_suggestion(message: Message, state: FSMContext) -> None:
    await state.set_state(FeedbackFlow.suggestion)
    await message.answer(
        "💡 Предложение\n\n"
        "Напишите сообщение полностью — оно будет скопировано и направлено "
        "администратору."
    )


@router.message(Command("report"))
async def cmd_report(message: Message, state: FSMContext) -> None:
    await state.set_state(FeedbackFlow.report)
    await message.answer(
        "🐞 Жалоба\n\n"
        "Напишите сообщение полностью — оно будет скопировано и направлено "
        "администратору."
    )


@router.message(FeedbackFlow.suggestion, F.text)
async def on_suggestion(message: Message, state: FSMContext) -> None:
    await state.clear()
    if await _forward_feedback(message, "💡 Предложение"):
        await message.answer("Спасибо! Сообщение отправлено администратору.")
    else:
        await message.answer("Администратор временно недоступен, попробуйте позже.")


@router.message(FeedbackFlow.report, F.text)
async def on_report(message: Message, state: FSMContext) -> None:
    await state.clear()
    if await _forward_feedback(message, "🐞 Жалоба"):
        await message.answer("Спасибо! Сообщение отправлено администратору.")
    else:
        await message.answer("Администратор временно недоступен, попробуйте позже.")


def build_router() -> Router:
    return router
