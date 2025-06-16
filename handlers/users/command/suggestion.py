from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from sqlalchemy.orm import sessionmaker

from services.notification import NotificationService
from states.suggestionState import SuggestionState

router = Router(name=__name__)

@router.message(Command('suggestion'))
async def suggestion_command(msg: Message, state: FSMContext, session: sessionmaker):
    await state.set_state(SuggestionState.message_wait)
    await msg.answer('Введите ваше предложение:\n\n*Если передумали отправлять сообщение или что-то ещё, просто введите /start, чтобы не отправить выйти из режима отправки предложений')

@router.message(SuggestionState.message_wait)
async def suggestion_command(msg: Message, state: FSMContext, session: sessionmaker):
    if msg.text == '/start':
        await state.clear()
        return
    message = (f'ОТПРАВЛЕНО ПРЕДЛОЖЕНИЕ ПО УЛУЧШЕНИЮ БОТА\n'
               f'От: @{msg.from_user.username} <code>{msg.from_user.id}</code> {msg.from_user.first_name} {msg.from_user.last_name}\n'
               f'\n{msg.text}')
    notification_service = NotificationService(session)
    await notification_service.notify_admins(message=message)
    await state.clear()
    await msg.answer('Спасибо!\n'
                     'Ваше предложение было отправлено!')