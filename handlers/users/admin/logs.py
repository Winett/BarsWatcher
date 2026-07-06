import re

from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from filters.admin import AdminFilter
from services.log_service import LogService

router = Router(name=__name__)


class LogsState(StatesGroup):
    wait_date = State()


@router.message(AdminFilter(), Command('logs'))
async def logs_command(msg: Message, command: CommandObject):
    logs = LogService.get_available_logs()
    if not logs:
        await msg.answer("Нет доступных логов")
        return

    lines = ["Доступные логи:\n"]
    for log in logs:
        date_formatted = log["date"]
        lines.append(f"  {date_formatted}  ({log['size']})")
    lines.append("\nОтправить: /logs <дата>\nПример: /logs 2026-07-06")

    await msg.answer("\n".join(lines), parse_mode=None)


@router.message(AdminFilter(), Command('logs_date'))
async def logs_date_command(msg: Message, state: FSMContext):
    await state.set_state(LogsState.wait_date)
    await msg.answer("Введите дату лога в формате YYYY-MM-DD:")


@router.message(LogsState.wait_date)
async def logs_date_input(msg: Message, state: FSMContext):
    if msg.text and msg.text.startswith('/'):
        await state.clear()
        return

    date_str = msg.text.strip() if msg.text else ""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        await msg.answer("Неверный формат. Используйте YYYY-MM-DD (например, 2026-07-06)", parse_mode=None)
        return

    await state.clear()
    await LogService.send_log_by_date(date_str, msg.bot, msg.chat.id)
