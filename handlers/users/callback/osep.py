from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram import Router, F

from keyboards.inline import input_osep_data_keyboard, update_osep_data_keyboard
from sqlalchemy.orm import sessionmaker
from services.user import UserService
from settings import WORKDIR

from watchers.managers.watcher_manager import OsepWatcherManager
from watchers.models.watcher_models import UserCredentials, WatcherType
from watchers.services.watcher_factory import WatcherFactory
from watchers.core.exceptions import Auth2FA
from watchers.connectors.connection_monitor import OsepMonitor
from states.osepState import OsepState

from loguru import logger

router = Router(name=__name__)

@router.callback_query(F.data == 'osep')
async def osep_command(msg: CallbackQuery, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    used_osep = await user_service.check_osep(msg.from_user.id)

    msg_to_send = f'Отслеживание ОСЭП: {"✅" if used_osep else "❌"}\n'
    if used_osep:
        msg_to_send += f'Текущие параметры, по которым отслеживается ОСЭП:\n' \
                       f' - Логин: {await user_service.get_osep_login(msg.from_user.id)}'
        await msg.message.answer(msg_to_send, reply_markup=update_osep_data_keyboard(used_osep))
        return
    else:
        if not (await user_service.exist_osep_credentials(msg.from_user.id)):
            msg_to_send += '\n\nДобавьте свой логин и пароль по кнопке ниже:'
            await msg.message.answer(msg_to_send, reply_markup=input_osep_data_keyboard())
            return
        msg_to_send += ('ОСЭП не отслеживается\n'
                        'Имеются сохранённые параметры, по которым отслеживается ОСЭП\n'
                        f'   - Логин: {await user_service.get_osep_login(msg.from_user.id)}\n')
        await msg.message.answer(msg_to_send, reply_markup=update_osep_data_keyboard(used_osep))
    await msg.answer()
    return

@router.callback_query(F.data == 'osep_credentials')
async def osep_credentials_command(msg: CallbackQuery, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    if await user_service.check_osep(msg.from_user.id):
        await OsepWatcherManager.stop_and_delete(msg.from_user.id)
        await user_service.set_osep_status_used(msg.from_user.id, False)
    await msg.message.answer('Введите Логин ОСЭП:')
    await state.set_state(OsepState.osep_login)
    await msg.answer()

    return

@router.message(OsepState.osep_login)
async def osep_login_command(msg: Message, state: FSMContext, session: sessionmaker):
    await state.update_data(osep_login=msg.text)
    await msg.answer('Введите Пароль ОСЭП:')
    await state.set_state(OsepState.osep_password)
    return

@router.message(OsepState.osep_password)
async def osep_password_command(msg: Message, state: FSMContext, session: sessionmaker):
    osep_login = (await state.get_data()).get('osep_login')

    user_service = UserService(session)
    await user_service.set_osep(msg.from_user.id, osep_login, msg.text)
    await msg.delete()
    await state.clear()

    # Создаём auth и логинимся
    auth, session_obj = WatcherFactory.create_auth_and_session(
        user_id=msg.from_user.id,
        service="osep",
        login=osep_login,
        password=msg.text,
        watcher_type=WatcherType.OSEP
    )

    if not OsepMonitor().is_connected:
        await msg.answer("Сервер ОСЭП в данный момент недоступен. Попробуйте позже.")
        return

    try:
        res = await auth.login()
    except Exception as e:
        logger.error(f"Ошибка авторизации ОСЭП для {msg.from_user.id}: {e}")
        await msg.answer("Произошла ошибка при авторизации. Попробуйте позже.")
        return
    if not res:
        await msg.answer("Неверный логин или пароль")
        return

    # Создаём watcher через фабрику
    osep_watcher = await WatcherFactory.create_osep_watcher(msg.from_user.id, auth)
    await osep_watcher.start()
    await user_service.set_osep_status_used(msg.from_user.id, True)
    logger.info(f'Пользователь {msg.from_user.id} {msg.from_user.username} поставил отслеживание ОСЭП!')
    await msg.answer('Уведомления о ОСЭПе включены!')
    return

@router.callback_query(F.data == 'watching_osep')
async def watching_osep_command(msg: CallbackQuery, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    osep_login = await user_service.get_osep_login(msg.from_user.id)
    osep_password = await user_service.get_osep_password(msg.from_user.id)

    # Создаём auth и логинимся
    auth, session_obj = WatcherFactory.create_auth_and_session(
        user_id=msg.from_user.id,
        service="osep",
        login=osep_login,
        password=osep_password,
        watcher_type=WatcherType.OSEP
    )

    if not OsepMonitor().is_connected:
        await msg.answer("Сервер ОСЭП в данный момент недоступен. Попробуйте позже.")
        return

    try:
        res = await auth.login()
    except Exception as e:
        logger.error(f"Ошибка авторизации ОСЭП для {msg.from_user.id}: {e}")
        await msg.answer("Произошла ошибка при авторизации. Попробуйте позже.")
        return
    if not res:
        await msg.answer("Неверный логин или пароль")
        return

    osep_watcher = await WatcherFactory.create_osep_watcher(msg.from_user.id, auth)
    await osep_watcher.start()
    await user_service.set_osep_status_used(msg.from_user.id, True)
    logger.info(f'Пользователь {msg.from_user.id} {msg.from_user.username} поставил отслеживание ОСЭП!')
    await msg.answer('Уведомления о ОСЭПе включены!')
    await msg.message.edit_text(f'Отслеживание ОСЭП: {"✅"}\n'
                                f'Текущие параметры, по которым отслеживается ОСЭП:\n'
                                f' - Логин: {await user_service.get_osep_login(msg.from_user.id)}',
                                reply_markup=update_osep_data_keyboard(True))
    await msg.answer()
    return

@router.callback_query(F.data == 'dont_watching_osep')
async def watching_osep_command(msg: CallbackQuery, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    await OsepWatcherManager.stop_and_delete(msg.from_user.id)
    await user_service.set_osep_status_used(msg.from_user.id, False)
    logger.info(f'Пользователь {msg.from_user.id} {msg.from_user.username} снял отслеживание ОСЭП!')
    await msg.answer('Уведомления о ОСЭПе выключены!')
    await msg.message.edit_text(f'Отслеживание ОСЭП: {"❌"}\n'
                                f'Текущие параметры, по которым отслеживается ОСЭП:\n'
                                f' - Логин: {await user_service.get_osep_login(msg.from_user.id)}',
                                reply_markup=update_osep_data_keyboard(False))
    await msg.answer()
    return
