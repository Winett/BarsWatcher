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
               "/notify_user - уведомить одного пользователя\n"
               "/notify_all_users - уведомить всех пользователей\n\n"
               "Тестирование:\n"
               "/test_bars - тестовое уведомление БАРС\n"
               "/test_osep - тестовое уведомление ОСЭП\n\n"
               "Работа с пользователями:\n"
               "/get_user - получить информацию о пользователе\n"
               "/get_manager_status - получение состояний менеджеров\n\n"
               "Настройка вотчеров:\n"
               "/config_show - показать текущие глобальные настройки\n"
               "/config_global - изменить глобальный параметр\n"
               "/config_user - задать персональный параметр пользователю\n"
               "/config_bars - настройки БАРС (глобальные или для пользователя)\n\n"
               "Логи:\n"
               "/logs - список доступных логов\n"
               "/logs_date - запросить лог за дату")
    await msg.answer(message)