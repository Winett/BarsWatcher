from aiogram.types import FSInputFile
from loguru import logger
import os
import sys
from pathlib import Path
from datetime import timedelta, timezone

import asyncio
from aiogram import Bot

from concurrent.futures import ThreadPoolExecutor
import threading

from settings import settings
from functools import wraps
from services.log_service import LogService

executor = ThreadPoolExecutor(max_workers=2)
send_lock = threading.Lock()

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
    def patch_logger(record):
        record.update(time=record['time'].astimezone(timezone(timedelta(hours=3))))
        return record


    def retention_callback(files_name: list[str]):
        for file_name in files_name:
            executor.submit(send_log_sync, file_name)

    def send_log_sync(file_name: str):
        try:
            loop = asyncio.get_running_loop()
            loop.run_until_complete(
                LogService.archive_and_send(file_name, bot, admins)
            )
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(
                    LogService.archive_and_send(file_name, bot, admins)
                )
            finally:
                loop.close()
        except Exception as e:
            print(f"✗ Ошибка отправки sync: {e}")


    logger_config = {
        "handlers": [
            {
                "sink": sys.stdout,
                "format": "<white>{time:YYYY-MM-DD HH:mm:ss.SSS:Z}</white>"
                          " | <level>{level: <8}</level>"
                          " | {name}:{function}:{module}:{file}:{line}"
                          " - <magenta>{message}</magenta>",
                "level": "DEBUG",
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
                "level": "DEBUG",
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
