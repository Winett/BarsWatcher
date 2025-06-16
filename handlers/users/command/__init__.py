from aiogram import Router

from handlers.users.command.report import router as report_router
from handlers.users.command.suggestion import router as suggestion_router

__all__ = ['router']

router = Router(name=__name__)

router.include_routers(
    report_router,
    suggestion_router
)
