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
    CHUNK_SIZE = 40 * 1024 * 1024  # 40MB per chunk

    @classmethod
    def _ensure_dir(cls):
        cls.LOGS_DIR.mkdir(exist_ok=True)

    @classmethod
    def _archive_log(cls, file_name: str) -> list[Path]:
        log_path = Path(file_name)
        if not log_path.exists():
            return []

        date_str = datetime.now().strftime("%Y-%m-%d")
        file_size = log_path.stat().st_size
        cls._ensure_dir()

        if file_size <= cls.CHUNK_SIZE:
            zip_path = cls.LOGS_DIR / f"{date_str}.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.write(log_path, log_path.name)
            return [zip_path]

        chunks = []
        part = 0
        with open(log_path, "rb") as f:
            while True:
                data = f.read(cls.CHUNK_SIZE)
                if not data:
                    break
                part += 1
                chunk_zip = cls.LOGS_DIR / f"{date_str}_part{part}.zip"
                with zipfile.ZipFile(chunk_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr(log_path.name, data)
                chunks.append(chunk_zip)

        return chunks

    @classmethod
    async def archive_and_send(cls, file_name: str, bot: Bot, admins: list[int]):
        chunks = cls._archive_log(file_name)
        if not chunks:
            return

        total = len(chunks)
        date_str = datetime.now().strftime("%d.%m.%Y")

        try:
            for admin_id in admins:
                try:
                    for i, chunk in enumerate(chunks):
                        chunk_size = chunk.stat().st_size
                        caption = f"Лог бота за {date_str}"
                        if total > 1:
                            caption += f" (часть {i + 1}/{total})"
                        caption += f"\n\nРазмер: {cls._format_size(chunk_size)}"

                        await bot.send_document(
                            chat_id=admin_id,
                            document=FSInputFile(path=chunk),
                            caption=caption,
                        )
                except Exception as e:
                    logger.error(f"Ошибка отправки лога админу {admin_id}: {e}")

            logger.info(f"Лог заархивирован: {file_name} ({total} частей)")
        except Exception as e:
            logger.error(f"Ошибка архивации лога: {e}")
        finally:
            os.remove(file_name)
            for chunk in chunks:
                if chunk.exists():
                    chunk.unlink()

    @classmethod
    def cleanup_old_logs(cls, days: int = 7):
        cls._ensure_dir()
        cutoff = datetime.now() - timedelta(days=days)
        for zip_file in cls.LOGS_DIR.glob("*.zip"):
            try:
                file_date = datetime.strptime(zip_file.stem.split("_part")[0], "%Y-%m-%d")
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
                date_part = zip_file.stem.split("_part")[0]
                datetime.strptime(date_part, "%Y-%m-%d")
                logs.append({
                    "date": date_part,
                    "path": zip_file,
                    "size": cls._format_size(zip_file.stat().st_size),
                })
            except ValueError:
                pass
        return logs

    @classmethod
    async def send_log_by_date(cls, date_str: str, bot: Bot, chat_id: int):
        parts = sorted(cls.LOGS_DIR.glob(f"{date_str}_part*.zip"))
        single = cls.LOGS_DIR / f"{date_str}.zip"

        if not parts and not single.exists():
            await bot.send_message(chat_id, f"Лог за {date_str} не найден", parse_mode=None)
            return

        files = parts if parts else [single]
        total = len(files)

        for i, f in enumerate(files):
            chunk_size = f.stat().st_size
            caption = f"Лог бота за {date_str}"
            if total > 1:
                caption += f" (часть {i + 1}/{total})"
            caption += f"\n\nРазмер: {cls._format_size(chunk_size)}"

            await bot.send_document(
                chat_id=chat_id,
                document=FSInputFile(path=f),
                caption=caption,
            )

    @staticmethod
    def _format_size(size: int) -> str:
        for unit in ("Б", "КБ", "МБ", "ГБ"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} ТБ"
