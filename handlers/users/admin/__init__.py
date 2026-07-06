from aiogram import Router

from handlers.users.admin.notify_all_users import router as notify_all_users_router
from handlers.users.admin.notify_one_user import router as notify_one_user_router
from handlers.users.admin.admin import router as admin_router
from handlers.users.admin.get_user import router as get_user_router
from handlers.users.admin.get_manager_status import router as get_manager_status_router
from handlers.users.admin.config import router as config_router
from handlers.users.admin.test_notification import router as test_notification_router
from handlers.users.admin.logs import router as logs_router

__all__ = ['router']

router = Router(name=__name__)

router.include_routers(
    admin_router,
    notify_all_users_router,
    notify_one_user_router,
    get_user_router,
    get_manager_status_router,
    config_router,
    test_notification_router,
    logs_router,
)