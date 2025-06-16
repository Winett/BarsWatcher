from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from sqlalchemy.orm import sessionmaker

from services.user import UserService
from services.notification import NotificationService
from states.notifyState import NotifyOneUserState
from keyboards.inline import  confirm_keyboard
from filters.admin import AdminFilter

router = Router(name=__name__)


@router.message(AdminFilter(), Command('notify_user'))
async def notify_user_command(msg: Message, state: FSMContext, session: sessionmaker):
    await state.set_state(NotifyOneUserState.user_id_wait)
    await msg.answer('Введите user_id пользователя для отправки сообщения:')

@router.message(AdminFilter(), NotifyOneUserState.user_id_wait)
async def notify_user_command(msg: Message, state: FSMContext, session: sessionmaker):
    user_service = UserService(session)
    try:
        if not await user_service.is_exists(int(msg.text)):
            await msg.answer('Такого пользователя не существует!\nОтправьте user_id заново:')
            return
    except ValueError:
        await msg.answer('Ошибка ввода id пользователя\nОтправьте user_id заново:')
        return

    await state.update_data(user_id=msg.text)
    await state.set_state(NotifyOneUserState.message_wait)
    await msg.answer('Введите сообщение для пользователя:')

@router.message(AdminFilter(), NotifyOneUserState.message_wait)
async def notify_user_command(msg: Message, state: FSMContext, session: sessionmaker):
    await state.update_data(message=msg.text)
    await state.set_state(NotifyOneUserState.confirmation)
    await msg.answer(f'Подтвердите отправку сообщения:\n\n{msg.text}', reply_markup=confirm_keyboard())

@router.callback_query(AdminFilter(), F.data == 'confirm', NotifyOneUserState.confirmation)
async def notify_user_command(msg: CallbackQuery, state: FSMContext, session: sessionmaker):
    notification_service = NotificationService(session)
    user_id = (await state.get_data()).get('user_id')
    message = (await state.get_data()).get('message')

    await notification_service.notify_user(user_id, message)
    await msg.message.answer('Сообщение отправлено пользователю')
    await state.clear()
