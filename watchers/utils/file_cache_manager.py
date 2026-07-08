import asyncio
from pathlib import Path
from typing import Dict

from loguru import logger

from settings import WORKDIR

CACHE_DIR = WORKDIR / "cashed_files"
DEFAULT_TTL = 3600  # 1 час


class FileCacheManager:
    """Менеджер файлового кэша с автоматическим удалением по TTL.

    При сохранении файла планирует его удаление через asyncio.create_task.
    Не требует периодического сканирования директории.
    """

    _tasks: Dict[str, asyncio.Task] = {}

    @classmethod
    def schedule_removal(cls, filename: str, ttl: int = DEFAULT_TTL):
        """Запланировать удаление файла через ttl секунд."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        filepath = CACHE_DIR / filename

        # Если уже есть запланированное удаление для этого файла — отменяем
        if filename in cls._tasks and not cls._tasks[filename].done():
            cls._tasks[filename].cancel()

        cls._tasks[filename] = asyncio.create_task(
            cls._remove_after(filepath, filename, ttl)
        )
        logger.debug(f"Запланировано удаление {filename} через {ttl}с")

    @classmethod
    async def _remove_after(cls, filepath: Path, filename: str, ttl: int):
        """Удалить файл после задержки."""
        try:
            await asyncio.sleep(ttl)
            if filepath.exists():
                filepath.unlink()
                logger.info(f"Удалён файл кэша: {filename}")
        except asyncio.CancelledError:
            pass  # Файл ещё нужен, удаление отменено
        except Exception as e:
            logger.warning(f"Ошибка удаления {filename}: {e}")
        finally:
            cls._tasks.pop(filename, None)

    @classmethod
    async def cancel_all(cls):
        """Отменить все запланированные удаления (при shutdown)."""
        for task in cls._tasks.values():
            if not task.done():
                task.cancel()
        cls._tasks.clear()
