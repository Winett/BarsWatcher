from aiogram.fsm.state import State, StatesGroup

class SuggestionState(StatesGroup):
    message_wait = State()