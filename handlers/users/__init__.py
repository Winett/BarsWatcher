from aiogram import Router

from handlers.users.start import router as start_router

__all__ = ['router']

router = Router(name=__name__)

router.include_routers(
    start_router
)