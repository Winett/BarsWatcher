from aiogram.fsm.state import State, StatesGroup

class BarsState(StatesGroup):
    bars_login = State()
    bars_password = State()