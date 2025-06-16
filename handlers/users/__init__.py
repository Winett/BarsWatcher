from aiogram import Router

from handlers.users.start import router as start_router
from handlers.users.callback import router as callback_router
from handlers.users.admin import router as admin_router
from handlers.users.command import router as command_router

__all__ = ['router']

router = Router(name=__name__)

router.include_routers(
    start_router,
    callback_router,
    admin_router,
    command_router
)