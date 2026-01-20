from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext

from sqlalchemy.orm import sessionmaker


from filters.admin import AdminFilter

router = Router(name=__name__)

@router.message(AdminFilter(), Command('admin'))
async def admin_command(msg: Message, state: FSMContext, session: sessionmaker):
    message = ("Список всех команд, которые доступны:\n\n"
               "Оповещения:\n"
               "/notify_user - уведомить одного пользователя; Требуется user_id пользователя и отправляемое сообщение\n"
               "/notify_all_users - уведомить всех пользователей; Требуется отправляемое сообщение\n\n"
               "Работа с пользователями:\n"
               "/get_user - получить информацию о пользователе; Требуется user_id пользователя\n"
               "/get_manager_status - получение состояний менеджеров")
    await msg.answer(message)