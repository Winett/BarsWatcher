import sys
from pathlib import Path

from loguru import logger

__all__ = ["setup_logging", "logger"]


def setup_logging(debug: bool = False) -> None:
    """Настройка loguru: консоль + ежедневный файл с ротацией."""
    logger.remove()
    logger.configure(extra={"component": "core"})

    level = "DEBUG" if debug else "INFO"
    logger.add(
        sys.stdout,
        level=level,
        colorize=True,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{extra[component]: <12}</cyan> | "
            "<dim>{name}:{line}</dim> | {message}"
        ),
    )

    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    logger.add(
        log_dir / "barswatcher_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="14 days",
        # DEBUG пишется в файл только при DEBUG=1 — иначе логи разрастаются
        level=level,
        encoding="utf-8",
        # модуль:строка — чтобы сразу видеть, где именно сработало
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {extra[component]: <12} | {name}:{line} | {message}",
    )
