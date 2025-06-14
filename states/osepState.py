from aiogram.fsm.state import State, StatesGroup

class OsepState(StatesGroup):
    osep_login = State()
    osep_password = State()