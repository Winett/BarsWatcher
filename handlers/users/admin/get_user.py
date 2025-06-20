from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest

from sqlalchemy.orm import sessionmaker

from filters.admin import AdminFilter

from states.getuserState import GetUserState

from services.user import UserService
from watchers.notificator import OsepNotificator, BarsNotificator

from keyboards.inline import enable_watching_keyboard

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
    message = (f"Пользователь с id {msg.text} @{user.username}\n\n"
                f"Отслеживание БАРС: {'✅' if user.used_bars else '❌'}\n"
                f"Отслеживание ОСЭП: {'✅' if user.used_osep else '❌'}\n")
    #TODO: сделать кнопки для включения и отключения оповещений?
    await msg.answer(message, reply_markup=enable_watching_keyboard(user.user_id,user.used_bars, user.used_osep))

@router.callback_query(F.data.startswith('dont_watching_bars_') | F.data.startswith('watching_osep_') | F.data.startswith('dont_watching_osep_') | F.data.startswith('watching_bars_') | F.data.startswith('dont_watching_bars_'))
async def change_watching(msg: CallbackQuery, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    user = await user_service.find_user(int(msg.data.split('_')[-1]))
    if msg.data.startswith('watching_osep_'):
        try:
            if await OsepNotificator(user.user_id, user.osep_login, user.osep_password).start_watching():
                user.used_osep = True
                await user_service.set_osep_status_used(user.user_id, True)
            else:
                await msg.answer("Произошла ошибка при включении отслеживания ОСЭП", show_alert=True)
        except TypeError as e: #если логин или пароль None
            await msg.answer("Произошла ошибка при включении отслеживания ОСЭП", show_alert=True)
    elif msg.data.startswith('dont_watching_osep_'):
        OsepNotificator.stop_watching(user.user_id)
        user.used_osep = False
        await user_service.set_osep_status_used(user.user_id, False)
    elif msg.data.startswith('watching_bars_'):
        try:
            if await BarsNotificator(user.user_id, user.bars_login, user.bars_password).start_watching():
                user.used_bars = True
                await user_service.set_bars_status_used(user.user_id, True)
            else:
                await msg.answer("Произошла ошибка при включении отслеживания БАРС", show_alert=True)
        except TypeError as e: #если логин или пароль None
            await msg.answer("Произошла ошибка при включении отслеживания БАРС", show_alert=True)
    elif msg.data.startswith('dont_watching_bars_'):
        BarsNotificator.stop_watching(user.user_id)
        user.used_bars = False
        await user_service.set_bars_status_used(user.user_id, False)
    try:
        await msg.message.edit_text(f"Пользователь с id {user.user_id} @{user.username}\n\n"
                f"Отслеживание БАРС: {'✅' if user.used_bars else '❌'}\n"
                f"Отслеживание ОСЭП: {'✅' if user.used_osep else '❌'}\n", reply_markup=enable_watching_keyboard(user.user_id,user.used_bars, user.used_osep))
    except TelegramBadRequest:
        pass
