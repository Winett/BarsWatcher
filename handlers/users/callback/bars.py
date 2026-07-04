from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram import Router, F

from keyboards.inline import input_bars_data_keyboard, update_bars_data_keyboard
from sqlalchemy.orm import sessionmaker
from services.user import UserService
from settings import WORKDIR

from states.barsState import BarsState
from watchers.models.watcher_models import UserCredentials, WatcherType
from watchers.services.cache_service import AsyncFileCacher
from watchers.services.watcher_factory import WatcherFactory
from watchers.session.pool_session import PoolSession
from watchers.auth.bars_auth import BarsAuth
from watchers.managers.watcher_manager import BarsWatcherManager
from watchers.core.exceptions import Auth2FA
from watchers.connectors.connection_monitor import BarsMonitor

from loguru import logger

router = Router(name=__name__)

@router.callback_query(F.data == 'bars')
async def bars_command(msg: CallbackQuery, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    used_bars = await user_service.check_bars(msg.from_user.id)

    msg_to_send = f'Отслеживание БАРС: {"✅" if used_bars else "❌"}\n'
    if used_bars:
        msg_to_send += f'Текущие параметры, по которым отслеживается БАРС:\n'\
                       f' - Логин: {await user_service.get_bars_login(msg.from_user.id)}'
        await msg.message.answer(msg_to_send, reply_markup=update_bars_data_keyboard(used_bars))
        return
    else:
        if not (await user_service.exist_bars_credentials(msg.from_user.id)):
            msg_to_send += '\n\nДобавьте свой логин и пароль по кнопке ниже:'
            await msg.message.answer(msg_to_send, reply_markup=input_bars_data_keyboard())
            return
        msg_to_send += ('БАРС не отслеживается\n'
                        'Имеются сохранённые параметры, по которым отслеживается БАРС\n'
                        f'   - Логин: {await user_service.get_bars_login(msg.from_user.id)}\n')
        await msg.message.answer(msg_to_send, reply_markup=update_bars_data_keyboard(used_bars))

    return

@router.callback_query(F.data == 'bars_credentials')
async def bars_credentials_command(msg: CallbackQuery, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    if await user_service.check_bars(msg.from_user.id):
        await BarsWatcherManager.stop_and_delete(msg.from_user.id)
        await user_service.set_bars_status_used(msg.from_user.id, False)
    await msg.message.answer('Введите Логин БАРС:')
    await state.set_state(BarsState.bars_login)

    await msg.answer()

@router.message(BarsState.bars_login)
async def bars_login_command(msg: Message, state: FSMContext, session: sessionmaker):
    await state.update_data(bars_login=msg.text)
    await msg.answer('Введите Пароль БАРС:')
    await state.set_state(BarsState.bars_password)
    return

@router.message(BarsState.bars_password)
async def bars_password_command(msg: Message, state: FSMContext, session: sessionmaker):
    bars_login = (await state.get_data()).get('bars_login')

    user_service = UserService(session)
    await user_service.set_bars(msg.from_user.id, bars_login, msg.text)
    await msg.delete()
    await state.clear()

    # Создаём auth и логинимся
    auth, session_obj = WatcherFactory.create_auth_and_session(
        user_id=msg.from_user.id,
        service="bars",
        login=bars_login,
        password=msg.text,
        watcher_type=WatcherType.BARS
    )

    if not BarsMonitor().is_connected:
        await msg.answer("Сервер БАРС в данный момент недоступен. Попробуйте позже.")
        return

    try:
        res = await auth.login()
    except Auth2FA:
        await auth.send_2fa_code()
        await state.set_state(BarsState.af2_code)
        # Сохраняем auth в state для обработки 2FA
        await state.update_data(bars_auth=auth)
        logger.debug(f"Пользователь {msg.from_user.id} {msg.from_user.username} ожидает 2FA код")
        await msg.answer("Введите код 2FA")
        return

    if not res:
        await msg.answer("Неверный логин или пароль")
        return

    # Создаём watcher через фабрику
    cache = AsyncFileCacher(WORKDIR / "cache.json")
    bars_watcher = WatcherFactory.create_bars_watcher(msg.from_user.id, auth, cache)
    await bars_watcher.start()
    await user_service.set_bars_status_used(msg.from_user.id, True)
    logger.info(f'Пользователь {msg.from_user.id} {msg.from_user.username} поставил отслеживание БАРСа!')
    await msg.answer('Уведомления о БАРСе включены!')
    return

@router.message(BarsState.af2_code)
async def process_2fa_code_command(msg: Message, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)

    # Получаем auth из state
    state_data = await state.get_data()
    auth = state_data.get('bars_auth')

    if not auth:
        await msg.answer("Сессия авторизации истекла. Попробуйте снова.")
        await state.clear()
        return

    if not BarsMonitor().is_connected:
        await msg.answer("Сервер БАРС в данный момент недоступен. Попробуйте позже.")
        await state.clear()
        return

    res = await auth.verify_2fa_code(msg.text)
    if not res:
        await msg.answer("Не удалось авторизоваться, возможно, неверный код 2FA")
        await PoolSession.release(msg.from_user.id, "bars")
    else:
        logger.info(f'Пользователь {msg.from_user.id} {msg.from_user.username} поставил отслеживание БАРСа!')
        cache = AsyncFileCacher(WORKDIR / "cache.json")
        bars_watcher = WatcherFactory.create_bars_watcher(msg.from_user.id, auth, cache)
        await bars_watcher.start()
        await user_service.set_bars_status_used(msg.from_user.id, True)
        await msg.answer('Уведомления о БАРСе включены!')
    await state.clear()
    return


@router.callback_query(F.data == 'watching_bars')
async def watching_bars_command(msg: CallbackQuery, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    login = await user_service.get_bars_login(msg.from_user.id)
    password = await user_service.get_bars_password(msg.from_user.id)

    # Создаём auth и логинимся
    auth, session_obj = WatcherFactory.create_auth_and_session(
        user_id=msg.from_user.id,
        service="bars",
        login=login,
        password=password,
        watcher_type=WatcherType.BARS
    )

    if not BarsMonitor().is_connected:
        await msg.answer("Сервер БАРС в данный момент недоступен. Попробуйте позже.")
        return

    try:
        res = await auth.login()
    except Auth2FA:
        await auth.send_2fa_code()
        await state.set_state(BarsState.af2_code)
        await state.update_data(bars_auth=auth)
        logger.debug(f"Пользователь {msg.from_user.id} {msg.from_user.username} ожидает 2FA код")
        await msg.message.answer("Введите код 2FA")
        return

    if not res:
        await msg.answer("Неверный логин или пароль")
        return

    cache = AsyncFileCacher(WORKDIR / "cache.json")
    bars_watcher = WatcherFactory.create_bars_watcher(msg.from_user.id, auth, cache)
    await bars_watcher.start()
    await user_service.set_bars_status_used(msg.from_user.id, True)
    logger.info(f'Пользователь {msg.from_user.id} {msg.from_user.username} поставил отслеживание БАРСа!')
    await msg.answer('Уведомления о БАРСе включены!')
    await msg.message.edit_text(f'Отслеживание БАРС: {"✅"}\n'
                                f'Текущие параметры, по которым отслеживается БАРС:\n'
                                f' - Логин: {await user_service.get_bars_login(msg.from_user.id)}',
                                reply_markup=update_bars_data_keyboard(True))

@router.callback_query(F.data == 'dont_watching_bars')
async def watching_bars_command(msg: CallbackQuery, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    await BarsWatcherManager.stop_and_delete(msg.from_user.id)
    await user_service.set_bars_status_used(msg.from_user.id, False)
    logger.info(f'Пользователь {msg.from_user.id} {msg.from_user.username} снял отслеживание БАРСа!')
    await msg.answer('Уведомления о БАРСе выключены!')
    await msg.message.edit_text(f'Отслеживание БАРС: {"❌"}\n'
                                f'Текущие параметры, по которым отслеживается БАРС:\n'
                                f' - Логин: {await user_service.get_bars_login(msg.from_user.id)}',
                                reply_markup=update_bars_data_keyboard(False))
