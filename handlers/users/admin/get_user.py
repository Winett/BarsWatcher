from aiogram import Router, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery
from sqlalchemy.orm import sessionmaker

from filters.admin import AdminFilter
from keyboards.inline import enable_watching_keyboard
from services.user import UserService
from settings import WORKDIR
from states.getuserState import GetUserState
from watchers import WatcherType
from watchers.models.watcher_models import UserCredentials
from watchers.services.cache_service import AsyncFileCacher
# from watchers.notificator import OsepNotificator, BarsNotificator
from watchers.watchers import OsepWatcher, BarsWatcher
from watchers.managers.watcher_manager import BarsWatcherManager, OsepWatcherManager

router = Router(name=__name__)


@router.message(AdminFilter(), Command('get_user'))
async def get_user_command(msg: Message, state: FSMContext, session: sessionmaker):
    await msg.answer('Введите user_id пользователя:')
    await state.set_state(GetUserState.user_id_wait)


@router.message(AdminFilter(), GetUserState.user_id_wait)
async def get_user_id_command(msg: Message, state: FSMContext, session: sessionmaker):
    await state.clear()
    user_service = UserService(session)
    user = await user_service.find_user(int(msg.text))
    if user is None:
        await msg.answer('Пользователь не найден!')
        return
    bars_watcher = BarsWatcherManager.get_watcher_instance(msg.from_user.id)
    osep_watcher = OsepWatcherManager.get_watcher_instance(msg.from_user.id)

    message = (f"Пользователь с id {msg.text} @{user.username}\n\n"
               f"Отслеживание БАРС: {'✅' if user.used_bars else '❌'}\n"
               f"Отслеживание ОСЭП: {'✅' if user.used_osep else '❌'}\n"
               )
    if bars_watcher:
        bars_stats = bars_watcher.stats
        message += (
            f"\n"
            f"Состояние BarsWatcher: \n"
            f"\t\tСтатус: {bars_stats.status.value}\n"
            f"\t\tВремя последнего запроса: {bars_stats.last_fetch_time}\n"
            f"\t\tПоследняя авторизация: {bars_stats.last_auth_time}\n"
            f"\t\tКоличество ошибок: {bars_stats.error_count}\n"
            f"\t\tHealthy: {bars_stats.is_healthy}\n"
            f"\t\tВремя последней ошибки: {bars_stats.last_error_time}\n"
            f"\t\tВремя работы: {bars_stats.uptime}с\n"
        )
    if osep_watcher:
        osep_stats = osep_watcher.stats
        message += (
            f"\n"
            f"Состояние OsepWatcher: \n"
            f"\t\tСтатус: {osep_stats.status.value}\n"
            f"\t\tВремя последнего запроса: {osep_stats.last_fetch_time}\n"
            f"\t\tПоследняя авторизация: {osep_stats.last_auth_time}\n"
            f"\t\tКоличество ошибок: {osep_stats.error_count}\n"
            f"\t\tHealthy: {osep_stats.is_healthy}\n"
            f"\t\tВремя последней ошибки: {osep_stats.last_error_time}\n"
            f"\t\tВремя работы: {osep_stats.uptime}с\n"
        )
    await msg.answer(message, reply_markup=enable_watching_keyboard(user.user_id, user.used_bars, user.used_osep))

@router.callback_query(F.data.startswith('dont_watching_bars_') | F.data.startswith('watching_osep_') | F.data.startswith('dont_watching_osep_') | F.data.startswith('watching_bars_') | F.data.startswith('dont_watching_bars_'))
async def change_watching(msg: CallbackQuery, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    user = await user_service.find_user(int(msg.data.split('_')[-1]))
    if msg.data.startswith('watching_osep_'):
        try:
            # manager = WatcherManagerFactory.get_manager(OsepWatcher)
            # osep_watcher = OsepWatcher(user.osep_login, user.osep_password, user.user_id, TelegramNotificator(user.user_id))
            osep_watcher = OsepWatcher(
                credentials=UserCredentials(username=user.osep_login, password=user.osep_password, user_id=user.user_id, watcher_type=WatcherType.OSEP),
                cache_service = AsyncFileCacher(WORKDIR / "cache.json")
            )
            # manager.add(user.user_id, osep_watcher)
            await osep_watcher.start()
            await user_service.set_osep_status_used(user.user_id, True)
            # if await manager.start(user.user_id):
            #     user.used_osep = True
            #     await user_service.set_osep_status_used(user.user_id, True)
            # else:
            #     await msg.answer("Произошла ошибка при включении отслеживания ОСЭП", show_alert=True)
        except TypeError as e: #если логин или пароль None
            await msg.answer("Произошла ошибка при включении отслеживания ОСЭП", show_alert=True)
    elif msg.data.startswith('dont_watching_osep_'):
        # manager = WatcherManagerFactory.get_manager(OsepWatcher)
        # await manager.stop(msg.from_user.id)
        await OsepWatcherManager.get_watcher_instance(msg.from_user.id).stop()
        OsepWatcherManager.unregister_watcher(msg.from_user.id)
        # OsepNotificator.stop_watching(user.user_id)
        # user.used_osep = False
        await user_service.set_osep_status_used(user.user_id, False)
    elif msg.data.startswith('watching_bars_'):
        try:
            # manager = WatcherManagerFactory.get_manager(BarsWatcher)
            bars_watcher = BarsWatcher(
                credentials=UserCredentials(username=user.bars_login, password=user.bars_password, user_id=user.user_id, watcher_type=WatcherType.BARS),
                cache_service = AsyncFileCacher(WORKDIR / "cache.json")
            )
            # manager.add(user.user_id, bars_watcher)
            await bars_watcher.start()
            await user_service.set_bars_status_used(user.user_id, True)
            # if await manager.start(user.user_id):
            #     user.used_bars = True
            #     await user_service.set_bars_status_used(user.user_id, True)
            # else:
            #     await msg.answer("Произошла ошибка при включении отслеживания БАРС", show_alert=True)
        except TypeError as e: #если логин или пароль None
            await msg.answer("Произошла ошибка при включении отслеживания БАРС", show_alert=True)
    elif msg.data.startswith('dont_watching_bars_'):
        await BarsWatcherManager.stop_and_delete(msg.from_user.id)
        user.used_bars = False
        await user_service.set_bars_status_used(user.user_id, False)
    try:
        await msg.message.edit_text(f"Пользователь с id {user.user_id} @{user.username}\n\n"
                f"Отслеживание БАРС: {'✅' if user.used_bars else '❌'}\n"
                f"Отслеживание ОСЭП: {'✅' if user.used_osep else '❌'}\n", reply_markup=enable_watching_keyboard(user.user_id,user.used_bars, user.used_osep))
    except TelegramBadRequest:
        pass
