from aiogram import Router

from handlers.users.callback.bars import router as bars_router
from handlers.users.callback.osep import router as osep_router

router = Router(name=__name__)

router.include_routers(
    bars_router,
    osep_router
)