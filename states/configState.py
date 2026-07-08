from aiogram.fsm.state import State, StatesGroup


class ConfigState(StatesGroup):
    waiting_param = State()        # выбор параметра для глобального
    waiting_value = State()        # ввод нового значения
    waiting_user_id = State()      # ввод user_id для персонального
    waiting_user_param = State()   # выбор параметра для пользователя
    waiting_user_value = State()   # ввод значения для пользователя
