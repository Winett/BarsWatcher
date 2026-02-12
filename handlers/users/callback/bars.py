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

# from watchers.notificator import BarsNotificator
# from watchers import TelegramNotificator
# from watchers import BarsWatcher, BarsWatcherManager, WatcherManagerFactory
from watchers.watchers import BarsWatcher, OsepWatcher
from watchers.managers.watcher_manager import BarsWatcherManager, OsepWatcherManager
from watchers.fetchers.bars_fetcher import BarsFetcher
from watchers.utils.exceptions import Auth2FA

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
        # BarsWatcherManager.get_instance().stop(msg.from_user.id, BarsWatcher.__class__.__name__)
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
    # await user_service.set_bars_status_used(msg.from_user.id, True)
    # await msg.answer('Данные для входа в БАРС сохранены!\n'
    #                  'Жми на /start и выбирай "Оповещения БАРС" -> "Отслеживать БАРС"')
    await state.clear()
    # manager = WatcherManagerFactory.get_manager(BarsWatcher)
    # bars_watcher = BarsWatcher(bars_login, msg.text, msg.from_user.id, TelegramNotificator(msg.from_user.id))
    user_creds = UserCredentials(username=bars_login, password=msg.text, user_id=msg.from_user.id,
                                    watcher_type=WatcherType.BARS)
    fetcher = BarsFetcher(user_creds)

    try:
        res = await fetcher.login()
    except Auth2FA:
        await fetcher.send_2fa_code()
        await state.set_state(BarsState.af2_code)
        logger.debug(f"Пользователь {msg.from_user.id} {msg.from_user.username} ожидает 2FA код")
        await msg.answer("Введите код 2FA")
        return

    if not res:
        await msg.answer("Неверный логин или пароль")
        return

    bars_watcher = BarsWatcher(
        credentials=user_creds,
        fetcher_service=fetcher,
        cache_service=AsyncFileCacher(WORKDIR / "cache.json")
    )
    # if not (await bars_watcher.test_login()):
    #     await msg.answer("Не удалось авторизоваться - неверный логин или пароль")
    #     return
    await bars_watcher.start()
    # manager.add(msg.from_user.id, bars_watcher)
    # if not (await manager.start(msg.from_user.id)):
    #     return
    await user_service.set_bars_status_used(msg.from_user.id, True)
    logger.info(f'Пользователь {msg.from_user.id} {msg.from_user.username} поставил отслеживание БАРСа!')
    await msg.answer('Уведомления о БАРСе включены!')
    return

@router.message(BarsState.af2_code)
async def process_2fa_code_command(msg: Message, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    fetcher: BarsFetcher = BarsFetcher.get_fetcher_instance(msg.from_user.id)
    res = await fetcher.verify_2fa_code(msg.text)
    if not res:
        await msg.answer("Не удалось авторизроваться, возможно, неверный код 2FA")
        await fetcher.close()
    else:
        logger.info(f'Пользователь {msg.from_user.id} {msg.from_user.username} поставил отслеживание БАРСа!')
        user = await user_service.find_user(user_id=msg.from_user.id)
        user_creds = UserCredentials(username=user.bars_login, password=user.bars_password, user_id=msg.from_user.id, watcher_type=WatcherType.BARS)
        bars_watcher = BarsWatcher(
            credentials=user_creds,
            fetcher_service=fetcher,
            cache_service=AsyncFileCacher(WORKDIR / "cache.json")
        )
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
    # manager = WatcherManagerFactory.get_manager(BarsWatcher)
    # bars_watcher = BarsWatcher(login, password, msg.from_user.id, TelegramNotificator(msg.from_user.id))
    user_creds = UserCredentials(username=login, password=password, user_id=msg.from_user.id,
                                 watcher_type=WatcherType.BARS)
    fetcher = BarsFetcher(user_creds)

    try:
        res = await fetcher.login()
    except Auth2FA:
        await fetcher.send_2fa_code()
        await state.set_state(BarsState.af2_code)
        logger.debug(f"Пользователь {msg.from_user.id} {msg.from_user.username} ожидает 2FA код")
        await msg.message.answer("Введите код 2FA")
        return

    if not res:
        await msg.answer("Неверный логин или пароль")
        return
    bars_watcher = BarsWatcher(
        credentials=user_creds,
        fetcher_service=fetcher,
        cache_service=AsyncFileCacher(WORKDIR / "cache.json")
    )
    # manager.add(msg.from_user.id, bars_watcher)
    # if not (await manager.start(msg.from_user.id)):
    #     return
    await bars_watcher.start()
    await user_service.set_bars_status_used(msg.from_user.id, True)
    logger.info(f'Пользователь {msg.from_user.id} {msg.from_user.username} поставил отслеживание БАРСа!')
    # await msg.message.answer('Уведомления о БАРСе включены!')
    await msg.answer('Уведомления о БАРСе включены!')
    await msg.message.edit_text(f'Отслеживание БАРС: {"✅"}\n'
                                f'Текущие параметры, по которым отслеживается БАРС:\n'
                                f' - Логин: {await user_service.get_bars_login(msg.from_user.id)}',
                                reply_markup=update_bars_data_keyboard(True))

@router.callback_query(F.data == 'dont_watching_bars')
async def watching_bars_command(msg: CallbackQuery, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    # manager = WatcherManagerFactory.get_manager(BarsWatcher)
    # await manager.stop(msg.from_user.id)
    await BarsWatcherManager.stop_and_delete(msg.from_user.id)
    # BarsNotificator.stop_watching(msg.from_user.id)
    await user_service.set_bars_status_used(msg.from_user.id, False)
    # await msg.message.answer('Уведомления о БРАСе выключены!')
    logger.info(f'Пользователь {msg.from_user.id} {msg.from_user.username} снял отслеживание БАРСа!')
    await msg.answer('Уведомления о БАРСе выключены!')
    await msg.message.edit_text(f'Отслеживание БАРС: {"❌"}\n'
                                f'Текущие параметры, по которым отслеживается БАРС:\n'
                                f' - Логин: {await user_service.get_bars_login(msg.from_user.id)}',
                                reply_markup=update_bars_data_keyboard(False))
