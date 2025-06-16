from aiogram.fsm.state import State, StatesGroup

class NotifyState(StatesGroup):
    message_wait = State()
    confirmation = State()

class NotifyOneUserState(StatesGroup):
    user_id_wait = State()
    message_wait = State()
    confirmation = State()
