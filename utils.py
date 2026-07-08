from loguru import logger
import sys
from datetime import timedelta, timezone

import asyncio
from aiogram import Bot

from functools import wraps
from services.log_service import LogService
from settings import settings


def logger_wraps(*, entry=True, exit=True, level="DEBUG"):

    def wrapper(func):
        name = func.__name__

        @wraps(func)
        def wrapped(*args, **kwargs):
            logger_ = logger.opt(depth=1)
            if entry:
                logger_.log(level, f"Вызывается '{name}' (args={args}, kwargs={kwargs})")
            result = func(*args, **kwargs)
            if exit:
                logger_.log(level, f"Выход '{name}' (result={result})")
            return result

        return wrapped

    return wrapper

def setup_logger(bot: Bot, admins: list[int]):
    loop = asyncio.get_running_loop()
    log_level = "DEBUG" if settings.DEBUG else "INFO"

    def patch_logger(record):
        record.update(time=record['time'].astimezone(timezone(timedelta(hours=3))))
        return record

    def retention_callback(files_name: list[str]):
        for file_name in files_name:
            asyncio.run_coroutine_threadsafe(
                LogService.archive_and_send(file_name, bot, admins),
                loop,
            )

    logger_config = {
        "handlers": [
            {
                "sink": sys.stdout,
                "format": "<white>{time:YYYY-MM-DD HH:mm:ss.SSS:Z}</white>"
                          " | <level>{level: <8}</level>"
                          " | {name}:{function}:{module}:{file}:{line}"
                          " - <magenta>{message}</magenta>",
                "level": log_level,
                "backtrace": True,
                "diagnose": True,
                "enqueue": True,
            },
            {
                "sink": "log.log",
                "format": "<white>{time:YYYY-MM-DD HH:mm:ss.SSS:Z}</white>"
                          " | <level>{level: <8}</level>"
                          " | {name}:{function}:{module}:{file}:{line}"
                          " - <magenta>{message}</magenta>",
                "level": log_level,
                "rotation": "00:00",
                "backtrace": True,
                "diagnose": True,
                "retention": retention_callback,
                "enqueue": True,
            }

        ],
        "patcher": patch_logger
    }
    logger.remove()
    logger.configure(**logger_config)
