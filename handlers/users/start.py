from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram import Router

from keyboards.inline import get_start_keyboard
from sqlalchemy.orm import sessionmaker
from services.user import UserService

from loguru import logger

router = Router(name=__name__)

@router.message(CommandStart())
async def start_command(msg: Message, state: FSMContext, session: sessionmaker):
    await state.clear()
    user_service = UserService(session)
    if not await user_service.is_exists(msg.from_user.id):
        await user_service.create(msg.from_user.id)
        logger.info(f'Появился новый пользователь! '
                    f'user_id = {msg.from_user.id} username = {msg.from_user.username} '
                    f'{msg.from_user.first_name = } {msg.from_user.last_name = }')

    await msg.answer('Добро пожаловать в бот для отслеживания изменений на ОСЭП и БАРС\n'
                     # 'Данный бот не является официальным приложение ФГБОУ ВО НИУ МЭИ и является энтузиастским проектом'
                     'Выберите, чтобы вы хотели начать отслеживать:',
                     reply_markup=get_start_keyboard()
                     )



