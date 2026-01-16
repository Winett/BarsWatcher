from aiogram import Bot, Dispatcher
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

WEBHOOK_HOST = "0.0.0.0"
WEBHOOK_PORT = 8080
WEBHOOK_PATH = "/webhook"
# WEBHOOK_SECRET_TOKEN = ""
# WEBHOOK_BASE_URL = "https://preoccipital-numbers-unfulgently.ngrok-free.dev"
WEBHOOK_BASE_URL = ""






def create_prepared_webapp(bot: Bot, dp: Dispatcher) -> web.Application:
    app = web.Application()

    webhook_requests_handler = SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        # secret_token=WEBHOOK_SECRET,
    )
    webhook_requests_handler.register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    return app