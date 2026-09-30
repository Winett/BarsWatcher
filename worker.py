"""Точка входа worker'а: ар-кю воркер с очередью проверок + планировщик."""

import asyncio

from arq.worker import run_worker

from src.core.config import settings
from src.core.logger import setup_logging
from src.workers.settings import WorkerSettings


def main() -> None:
    setup_logging(settings.debug)
    asyncio.run(run_worker(WorkerSettings))


if __name__ == "__main__":
    main()
