from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from sqlalchemy.orm import sessionmaker

from services.notification import NotificationService
from states.notifyState import NotifyState
from keyboards.inline import  confirm_keyboard
from filters.admin import AdminFilter

router = Router(name=__name__)



@router.message(AdminFilter(), Command('notify_all_users'))
async def notify_all_users_command(msg: Message, state: FSMContext, session: sessionmaker):

    await state.set_state(NotifyState.message_wait)
    await msg.answer('Введите сообщение для всех пользователей:')

#TODO: Сделать в будущем кнопки для переписывания текста
@router.message(AdminFilter(), NotifyState.message_wait)
async def notify_all_users_command(msg: Message, state: FSMContext, session: sessionmaker):
    await state.set_state(NotifyState.confirmation)
    await msg.answer(f'Подтвердите отправку сообщения всем пользователям:\n\n<code>{msg.text}</code>', reply_markup=confirm_keyboard())

    await state.update_data(message=msg.text)

@router.callback_query(AdminFilter(), F.data == 'confirm', NotifyState.confirmation)
async def notify_all_users_command(callback: CallbackQuery, state: FSMContext, session: sessionmaker):
    message = (await state.get_data()).get('message')
    await state.clear()
    notification_service = NotificationService(session)
    await notification_service.notify_all_users(message)
    await callback.message.answer('Сообщение отправлено всем пользователям')