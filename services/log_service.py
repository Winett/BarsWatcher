import os
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile
from loguru import logger


class LogService:
    LOGS_DIR = Path("logs")
    MAX_SIZE = 50 * 1024 * 1024  # 50MB Telegram limit

    @classmethod
    def _ensure_dir(cls):
        cls.LOGS_DIR.mkdir(exist_ok=True)

    @classmethod
    def _zip_log(cls, file_name: str) -> Path | None:
        log_path = Path(file_name)
        if not log_path.exists():
            return None

        date_str = datetime.now().strftime("%Y-%m-%d")
        zip_path = cls.LOGS_DIR / f"{date_str}.zip"

        cls._ensure_dir()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(log_path, log_path.name)

        return zip_path

    @classmethod
    async def archive_and_send(cls, file_name: str, bot: Bot, admins: list[int]):
        zip_path = cls._zip_log(file_name)
        if not zip_path:
            return

        zip_size = zip_path.stat().st_size
        date_str = datetime.now().strftime("%d.%m.%Y")

        try:
            if zip_size <= cls.MAX_SIZE:
                for admin_id in admins:
                    try:
                        await bot.send_document(
                            chat_id=admin_id,
                            document=FSInputFile(path=zip_path),
                            caption=f"Лог бота за {date_str}\n\nРазмер: {cls._format_size(zip_size)}",
                        )
                    except Exception as e:
                        logger.error(f"Ошибка отправки лога админу {admin_id}: {e}")
            else:
                size_str = cls._format_size(zip_size)
                for admin_id in admins:
                    try:
                        await bot.send_message(
                            chat_id=admin_id,
                            text=f"Лог бота за {date_str} слишком большой ({size_str})\n"
                                 f"Запросите его вручную: /logs",
                        )
                    except Exception as e:
                        logger.error(f"Ошибка уведомления админа {admin_id}: {e}")

            logger.info(f"Лог заархивирован: {zip_path}")
        except Exception as e:
            logger.error(f"Ошибка архивации лога: {e}")
        finally:
            os.remove(file_name)

    @classmethod
    def cleanup_old_logs(cls, days: int = 7):
        cls._ensure_dir()
        cutoff = datetime.now() - timedelta(days=days)
        for zip_file in cls.LOGS_DIR.glob("*.zip"):
            try:
                file_date = datetime.strptime(zip_file.stem, "%Y-%m-%d")
                if file_date < cutoff:
                    zip_file.unlink()
                    logger.info(f"Удалён старый лог: {zip_file.name}")
            except ValueError:
                pass

    @classmethod
    def get_available_logs(cls) -> list[dict]:
        cls._ensure_dir()
        logs = []
        for zip_file in sorted(cls.LOGS_DIR.glob("*.zip"), reverse=True):
            try:
                datetime.strptime(zip_file.stem, "%Y-%m-%d")
                logs.append({
                    "date": zip_file.stem,
                    "path": zip_file,
                    "size": cls._format_size(zip_file.stat().st_size),
                })
            except ValueError:
                pass
        return logs

    @classmethod
    async def send_log_by_date(cls, date_str: str, bot: Bot, chat_id: int):
        zip_path = cls.LOGS_DIR / f"{date_str}.zip"
        if not zip_path.exists():
            await bot.send_message(chat_id, f"Лог за {date_str} не найден")
            return

        await bot.send_document(
            chat_id=chat_id,
            document=FSInputFile(path=zip_path),
            caption=f"Лог бота за {date_str}\n\nРазмер: {cls._format_size(zip_path.stat().st_size)}",
        )

    @staticmethod
    def _format_size(size: int) -> str:
        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} ТБ"
