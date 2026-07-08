from datetime import timezone, timedelta, datetime

from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from sqlalchemy.orm import sessionmaker

from database.models import User
from filters.admin import AdminFilter
from keyboards.inline import enable_watching_keyboard
from services.user import UserService
from settings import WORKDIR
from states.getuserState import GetUserState
from watchers import WatcherType
from watchers.models.watcher_models import UserCredentials
from watchers.services.watcher_factory import WatcherFactory
from watchers.session.pool_session import PoolSession
from watchers.core.exceptions import Auth2FA
from watchers.watchers import OsepWatcher, BarsWatcher
from watchers.managers.watcher_manager import BarsWatcherManager, OsepWatcherManager

router = Router(name=__name__)


@router.message(AdminFilter(), Command('get_user'))
async def get_user_command(msg: Message, state: FSMContext, session: sessionmaker):
    await msg.answer('Введите user_id пользователя:')
    await state.set_state(GetUserState.user_id_wait)

def generate_status_user_watchers_message(user_id, username, used_bars, used_osep, bars_watcher, osep_watcher):
    message = (f"Пользователь с id <code>{user_id}</code> @{username}\n\n"
               f"Отслеживание БАРС: {'✅' if used_bars else '❌'}\n"
               f"Отслеживание ОСЭП: {'✅' if used_osep else '❌'}\n"
               )
    def format_uptime(seconds: float) -> str:
        td = timedelta(seconds=seconds)

        days = td.days
        hours, remainder = divmod(td.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)

        parts = []
        if days > 0:
            parts.append(f"{days} дней")
        if hours > 0:
            parts.append(f"{hours} часов")
        if minutes > 0:
            parts.append(f"{minutes} минут")
        if seconds > 0 or not parts:
            parts.append(f"{seconds} секунд")

        return " ".join(parts)

    def format_datetime_to_timezone_3(dt: datetime | None):
        return dt.astimezone(timezone(timedelta(hours=3))).strftime("%Y-%m-%d %H:%M:%S") if dt else dt

    if bars_watcher:
        bars_stats = bars_watcher.stats
        message += (
            f"\n"
            f"Состояние BarsWatcher: \n"
            f"\t\tСтатус: {bars_stats.status.value}\n"
            f"\t\tВремя последнего запроса: {format_datetime_to_timezone_3(bars_stats.last_fetch_time)}\n"
            f"\t\tПоследняя авторизация: {format_datetime_to_timezone_3(bars_stats.last_auth_time)}\n"
            f"\t\tКоличество ошибок: {bars_stats.error_count}\n"
            f"\t\tHealthy: {bars_stats.is_healthy}\n"
            f"\t\tВремя последней ошибки: {format_datetime_to_timezone_3(bars_stats.last_error_time)}\n"
            f"\t\tВремя работы: {format_uptime(bars_stats.uptime)}\n"
        )
    if osep_watcher:
        osep_stats = osep_watcher.stats
        message += (
            f"\n"
            f"Состояние OsepWatcher: \n"
            f"\t\tСтатус: {osep_stats.status.value}\n"
            f"\t\tВремя последнего запроса: {format_datetime_to_timezone_3(osep_stats.last_fetch_time)}\n"
            f"\t\tПоследняя авторизация: {format_datetime_to_timezone_3(osep_stats.last_auth_time)}\n"
            f"\t\tКоличество ошибок: {osep_stats.error_count}\n"
            f"\t\tHealthy: {osep_stats.is_healthy}\n"
            f"\t\tВремя последней ошибки: {format_datetime_to_timezone_3(osep_stats.last_error_time)}\n"
            f"\t\tВремя работы: {format_uptime(osep_stats.uptime)}\n"
        )

    return message



@router.message(AdminFilter(), GetUserState.user_id_wait)
async def get_user_id_command(msg: Message, state: FSMContext, session: sessionmaker):
    await state.clear()
    user_service = UserService(session)
    user = await user_service.find_user(int(msg.text))
    if user is None:
        await msg.answer('Пользователь не найден!')
        return

    bars_watcher = BarsWatcherManager.get_watcher_instance(user.user_id)
    osep_watcher = OsepWatcherManager.get_watcher_instance(user.user_id)

    message = generate_status_user_watchers_message(user.user_id, user.username, user.used_bars, user.used_osep, bars_watcher, osep_watcher)

    await msg.answer(message, reply_markup=enable_watching_keyboard(user.user_id, user.used_bars, user.used_osep))

async def refresh_user_state_massage(msg: CallbackQuery, user: User):
    try:
        bars_watcher = BarsWatcherManager.get_watcher_instance(user.user_id)
        osep_watcher = OsepWatcherManager.get_watcher_instance(user.user_id)

        message = generate_status_user_watchers_message(user.user_id, user.username, user.used_bars, user.used_osep,
                                                        bars_watcher, osep_watcher)
        await msg.message.edit_text(message, reply_markup=enable_watching_keyboard(user.user_id,user.used_bars, user.used_osep))
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith('dont_watching_bars_') | F.data.startswith('watching_osep_') | F.data.startswith('dont_watching_osep_') | F.data.startswith('watching_bars_') | F.data.startswith('dont_watching_bars_') | F.data.startswith("refresh_user_state_massage"))
async def change_watching(msg: CallbackQuery, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    user = await user_service.find_user(int(msg.data.split('_')[-1]))
    if msg.data.startswith("refresh_user_state_massage"):
        await refresh_user_state_massage(msg, user)
        await msg.answer()
        return
    if msg.data.startswith('watching_osep_'):
        try:
            # Создаём auth и логинимся
            auth, session_obj = WatcherFactory.create_auth_and_session(
                user_id=user.user_id,
                service="osep",
                login=user.osep_login,
                password=user.osep_password,
                watcher_type=WatcherType.OSEP
            )
            res = await auth.login()
            if not res:
                await msg.answer("Не удалось авторизоваться у пользователя")
                return

            osep_watcher = await WatcherFactory.create_osep_watcher(user.user_id, auth)
            await osep_watcher.start()
            await user_service.set_osep_status_used(user.user_id, True)
        except TypeError as e:
            await msg.answer("Произошла ошибка при включении отслеживания ОСЭП", show_alert=True)
    elif msg.data.startswith('dont_watching_osep_'):
        await OsepWatcherManager.stop_and_delete(user.user_id)
        await user_service.set_osep_status_used(user.user_id, False)
    elif msg.data.startswith('watching_bars_'):
        try:
            # Создаём auth и логинимся
            auth, session_obj = WatcherFactory.create_auth_and_session(
                user_id=user.user_id,
                service="bars",
                login=user.bars_login,
                password=user.bars_password,
                watcher_type=WatcherType.BARS
            )
            try:
                res = await auth.login()
            except Auth2FA as e:
                await msg.answer("Включена 2FA, не удаётся авторизоваться")
                return
            if not res:
                await msg.answer("Не удалось авторизоваться у пользователя")

            bars_watcher = await WatcherFactory.create_bars_watcher(user.user_id, auth)
            await bars_watcher.start()
            await user_service.set_bars_status_used(user.user_id, True)
        except TypeError as e:
            await msg.answer("Произошла ошибка при включении отслеживания БАРС", show_alert=True)
    elif msg.data.startswith('dont_watching_bars_'):
        await BarsWatcherManager.stop_and_delete(user.user_id)
        await user_service.set_bars_status_used(user.user_id, False)

    await refresh_user_state_massage(msg, user)
