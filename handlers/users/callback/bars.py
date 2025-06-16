from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext
from aiogram import Router, F

from keyboards.inline import input_bars_data_keyboard, update_bars_data_keyboard
from sqlalchemy.orm import sessionmaker
from services.user import UserService

from states.barsState import BarsState

from watchers.notificator import BarsNotificator

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
        BarsNotificator.stop_watching(msg.from_user.id)
        await user_service.set_bars_status_used(msg.from_user.id, False)
    await msg.message.answer('Введите Логин БАРС:')
    await state.set_state(BarsState.bars_login)

    return

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
    await msg.answer('Данные для входа в БАРС сохранены!\n'
                     'Жми на /start и выбирай "Оповещения БАРС" -> "Отслеживать БАРС"')
    await state.clear()
    return

@router.callback_query(F.data == 'watching_bars')
async def watching_bars_command(msg: CallbackQuery, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    login = await user_service.get_bars_login(msg.from_user.id)
    password = await user_service.get_bars_password(msg.from_user.id)
    if not (await BarsNotificator(msg.from_user.id, login, password).start_watching()):
        return
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
    BarsNotificator.stop_watching(msg.from_user.id)
    await user_service.set_bars_status_used(msg.from_user.id, False)
    # await msg.message.answer('Уведомления о БРАСе выключены!')
    logger.info(f'Пользователь {msg.from_user.id} {msg.from_user.username} снял отслеживание БАРСа!')
    await msg.answer('Уведомления о БАРСе выключены!')
    await msg.message.edit_text(f'Отслеживание БАРС: {"❌"}\n'
                                f'Текущие параметры, по которым отслеживается БАРС:\n'
                                f' - Логин: {await user_service.get_bars_login(msg.from_user.id)}',
                                reply_markup=update_bars_data_keyboard(False))
