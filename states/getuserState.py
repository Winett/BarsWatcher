from aiogram.fsm.state import State, StatesGroup

class GetUserState(StatesGroup):
    user_id_wait = State()