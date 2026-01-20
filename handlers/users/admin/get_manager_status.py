from aiogram import Router
from aiogram.filters import Command

from aiogram.types import Message

from watchers.managers.watcher_manager import BarsWatcherManager, OsepWatcherManager
from filters.admin import AdminFilter

router = Router(name=__name__)

@router.message(AdminFilter(), Command('get_manager_status'))
async def get_user_command(msg: Message):
    message = ""

    bars_stats =  BarsWatcherManager.watcher_stats()
    if bars_stats:
        message += f"БАРС Менеджер\n"
        message += (f"Количество подключенных вотчеров: {bars_stats["count"]}\n\n"
                    f"Из них:\n")

        for status, count in bars_stats["watcher_status"].items():
            message += f"\t\t{status}: {count}\n"
        for status, users in bars_stats["non_running"].items():
            message += f"\t\t{status}: {users}\n"

    osep_stats =  OsepWatcherManager.watcher_stats()
    if osep_stats:
        message += f"\nОСЭП Менеджер\n"
        message += (f"Количество подключенных вотчеров: {osep_stats["count"]}\n\n"
                    f"Из них:\n")
        for status, count in osep_stats["watcher_status"].items():
            message += f"\t\t{status}: {count}\n"
        for status, users in osep_stats["non_running"].items():
            message += f"\t\t{status}: {users}\n"

    await msg.answer(message)
