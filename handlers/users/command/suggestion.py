from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from sqlalchemy.orm import sessionmaker

from services.notification import NotificationService
from states.suggestionState import SuggestionState
from handlers.users.command.forward import forward_to_admins

router = Router(name=__name__)


@router.message(Command('suggestion'))
async def suggestion_command(msg: Message, state: FSMContext, session: sessionmaker):
    await state.set_state(SuggestionState.message_wait)
    await msg.answer(
        'Введите ваше предложение:\n\n*Если передумали отправлять сообщение или что-то ещё, '
        'просто введите /start, чтобы не отправить выйти из режима отправки предложений')


@router.message(SuggestionState.message_wait)
async def suggestion_wait(
    msg: Message,
    state: FSMContext,
    session: sessionmaker,
    media_group_messages: list[Message] | None = None,
):
    if msg.text and msg.text == '/start':
        await state.clear()
        return

    header = (
        f'ОТПРАВЛЕНО ПРЕДЛОЖЕНИЕ ПО УЛУЧШЕНИЮ БОТА\n'
        f'От: @{msg.from_user.username} <code>{msg.from_user.id}</code> '
        f'{msg.from_user.first_name} {msg.from_user.last_name}'
    )

    notification_service = NotificationService(session)
    await forward_to_admins(notification_service.bot, msg, header, media_group_messages)

    await state.clear()
    await msg.answer('Спасибо!\nВаше предложение было отправлено!')
