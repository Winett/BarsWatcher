from aiogram import Router

from handlers.users import router as users_router


__all__ = ['router']

router = Router(name=__name__)
router.include_routers(
    users_router,
)