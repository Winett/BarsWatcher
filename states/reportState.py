from aiogram.fsm.state import State, StatesGroup

class ReportState(StatesGroup):
    message_wait = State()