"""Файлы логов: список дат и упаковка для отправки админу.

Логи пишутся в logs/barswatcher_YYYY-MM-DD.log (оба процесса). Для
отправки упаковываются в zip (с дроблением под лимит Telegram).
"""

from __future__ import annotations

import zipfile
from datetime import date, datetime
from pathlib import Path

__all__ = [
    "LOGS_DIR",
    "build_log_zips",
    "format_size",
    "list_log_dates",
    "logs_for",
]

LOGS_DIR = Path("logs")
MAX_SIZE = 50 * 1024 * 1024  # лимит Telegram на файл
CHUNK_SIZE = 40 * 1024 * 1024  # размер части при дроблении


def _parse_date(name: str) -> date | None:
    # barswatcher_2026-09-26.log → date(2026, 9, 26)
    parts = Path(name).stem.rsplit("_", 1)
    if len(parts) != 2:
        return None
    try:
        return datetime.strptime(parts[1], "%Y-%m-%d").date()
    except ValueError:
        return None


def list_log_dates() -> list[date]:
    """Даты, за которые есть логи (новые сверху). Только существующие."""
    if not LOGS_DIR.exists():
        return []
    days = {
        day
        for path in LOGS_DIR.glob("barswatcher_*.log")
        if (day := _parse_date(path.name)) is not None
    }
    return sorted(days, reverse=True)


def logs_for(day: date) -> list[Path]:
    """Файлы логов за конкретную дату (может быть пусто — лог уже удалён)."""
    if not LOGS_DIR.exists():
        return []
    return sorted(LOGS_DIR.glob(f"barswatcher_{day.isoformat()}.log"))


def build_log_zips(day: date) -> list[Path]:
    """Логи за дату в zip; пусто — если лога уже нет.

    Файл больше 50 МБ дробится на части (каждая часть — свой zip).
    """
    files = logs_for(day)
    if not files:
        return []
    out_dir = LOGS_DIR / "_send"
    out_dir.mkdir(exist_ok=True)
    date_str = day.isoformat()
    # прошлые попытки отправки этой даты — убираем
    for old in out_dir.glob(f"{date_str}*"):
        old.unlink(missing_ok=True)

    zip_path = out_dir / f"{date_str}.zip"
    # compresslevel=1: логи бывают большими, сжимаем быстро
    with zipfile.ZipFile(
        zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=1
    ) as archive:
        for path in files:
            try:
                archive.write(path, arcname=path.name)
            except OSError:
                continue
    if zip_path.stat().st_size <= MAX_SIZE:
        return [zip_path]

    zip_path.unlink()
    chunks: list[Path] = []
    for path in files:
        part = 0
        with open(path, "rb") as f:
            while True:
                data = f.read(CHUNK_SIZE)
                if not data:
                    break
                part += 1
                chunk_zip = out_dir / f"{path.stem}_part{part}.zip"
                with zipfile.ZipFile(chunk_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr(path.name, data)
                chunks.append(chunk_zip)
    return chunks


def format_size(size: int) -> str:
    value = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} ТБ"
