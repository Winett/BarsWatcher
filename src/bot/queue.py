"""Настройки arq-воркера очереди `bot`: доставка событий в Telegram-слой.

События (marks.changed, mail.received, auth.2fa_required, …) публикуются в
очередь `bot`; этот воркер запускается в процессе бота (bot.py) рядом с long
polling и вызывает обработчики, зарегистрированные через @subscribe — то есть
нотификатор Telegram. Дедупликация по event_id в dispatch_event.
"""

from __future__ import annotations

from arq.connections import RedisSettings
from arq.worker import Worker

from src.core.config import settings
from src.events import QUEUE_BOT, dispatch_event

__all__ = ["BotQueueSettings", "create_bot_worker"]


class BotQueueSettings:
    functions = [dispatch_event]
    queue_name = QUEUE_BOT

    redis_settings = RedisSettings.from_dsn(settings.redis_url)

    max_jobs = 20
    job_timeout = 120
    keep_result = 0
    retry_jobs = True
    max_tries = 3


def create_bot_worker() -> Worker:
    """Воркер очереди `bot` для запуска внутри event loop бота.

    В отличие от arq.run_worker (свой loop + свои сигналы), этот работает
    как обычная корутина: bot.py зовёт `await worker.async_run()` фоном.
    """
    return Worker(
        functions=BotQueueSettings.functions,
        queue_name=BotQueueSettings.queue_name,
        redis_settings=BotQueueSettings.redis_settings,
        max_jobs=BotQueueSettings.max_jobs,
        job_timeout=BotQueueSettings.job_timeout,
        keep_result=BotQueueSettings.keep_result,
        retry_jobs=BotQueueSettings.retry_jobs,
        max_tries=BotQueueSettings.max_tries,
        handle_signals=False,  # мы внутри чужого event loop (aiogram)
    )
