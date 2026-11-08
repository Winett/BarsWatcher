"""Точка входа worker'а: ар-кю воркер с очередью проверок + планировщик."""

import asyncio

from arq.worker import run_worker
from loguru import logger

from src.core.config import settings
from src.core.logger import setup_logging
from src.workers.settings import WorkerSettings


def main() -> None:
    setup_logging(settings.debug)
    try:
        asyncio.run(run_worker(WorkerSettings))
    except Exception:
        # любая необработанная ошибка — в лог-файл с traceback, дальше наверх
        logger.exception("worker остановлен с ошибкой")
        raise


if __name__ == "__main__":
    main()
