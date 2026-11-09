"""Точка входа worker'а: arq-воркер с очередью проверок + планировщик.

Устойчивость: если worker остановился с ошибкой (например, Redis перезапускался
и отвечал BusyLoadingError/ConnectionError) — процесс НЕ умирает, а перезапускается
с экспоненциальной паузой. Чистая остановка (сигнал) — выход.
"""

import time

from arq.worker import create_worker
from loguru import logger

from src.core.config import settings
from src.core.logger import setup_logging
from src.workers.settings import WorkerSettings, set_worker

RESTART_BACKOFF_MAX = 60.0
HEALTHY_RUN_SECONDS = 60.0  # столько прожил — считаем запуск успешным и сбрасываем паузу


def main() -> None:
    setup_logging(settings.debug)
    backoff = 5.0
    while True:
        started = time.monotonic()
        try:
            worker = create_worker(WorkerSettings)
            set_worker(worker)
            # сигналы (SIGINT/SIGTERM) обрабатывает arq — остановка будет чистой
            worker.run()
            return
        except Exception:
            logger.exception(
                "worker остановлен с ошибкой; перезапуск через {:.0f}с", backoff
            )
            if time.monotonic() - started > HEALTHY_RUN_SECONDS:
                backoff = 5.0  # долго работал — ошибка разовая, пытаемся сразу
            time.sleep(backoff)
            backoff = min(backoff * 2, RESTART_BACKOFF_MAX)


if __name__ == "__main__":
    main()
