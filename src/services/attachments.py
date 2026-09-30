"""Кэш вложений ОСЭП на диске.

Задумка: вложение скачивается один раз и живёт ограниченное время (TTL),
затем удаляется. Если файл уже есть в кэше — он прикрепляется сразу, без
повторного скачивания. Файлы >= 1 КБ кэшируются общими: один пользователь
скачал — остальные прикрепляют из кэша. Файлы < 1 КБ скачиваются отдельно
для каждого пользователя (дешевле скачать заново, чем разделять).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from loguru import logger

__all__ = ["AttachmentCache", "Downloader", "cleanup_loop"]

Downloader = Callable[[], Awaitable[bytes]]

_SAFE_NAME_RE = re.compile(r"[^\w.\- ()а-яёА-ЯЁ]+", re.UNICODE)
_SAFE_KEY_RE = re.compile(r"[^\w.-]+")


def _safe_name(filename: str) -> str:
    """Имя файла без пути и «плохих» символов (защита от path traversal)."""
    name = (filename or "file").replace("\\", "/").split("/")[-1]
    name = _SAFE_NAME_RE.sub("_", name).strip() or "file"
    return name[:120]


def _safe_key(key: str) -> str:
    return _SAFE_KEY_RE.sub("_", key or "unknown")[:120] or "unknown"


class AttachmentCache:
    """Дисковый кэш вложений: get_or_download + периодическая очистка."""

    def __init__(
        self,
        root: Path | str = "attachments_cache",
        ttl_seconds: float = 600,
        shared_min_size: int = 1024,
    ):
        self._root = Path(root)
        self._ttl = ttl_seconds
        self._shared_min = shared_min_size

    def _entry_dir(self, *, attachment_id: str, user_id: int, shared: bool) -> Path:
        key = _safe_key(attachment_id)
        if shared:
            return self._root / "shared" / key
        return self._root / f"user-{user_id}" / key

    def is_shared(self, size: int) -> bool:
        """Файл общего кэша (>= 1 КБ) или персонального (< 1 КБ)."""
        return size >= self._shared_min

    async def get_or_download(
        self,
        *,
        attachment_id: str,
        filename: str,
        size: int,
        user_id: int,
        download: Downloader,
    ) -> Path:
        """Путь к файлу: из кэша, либо скачанный и сохранённый в кэш."""
        entry = self._entry_dir(
            attachment_id=attachment_id,
            user_id=user_id,
            shared=self.is_shared(size),
        )
        path = entry / _safe_name(filename)
        if path.exists() and (time.time() - path.stat().st_mtime) < self._ttl:
            logger.debug(
                "вложение из кэша: {} (user={})", path.name, user_id
            )
            return path

        data = await download()
        entry.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        logger.info(
            "вложение скачано: {} | {} КБ | {} | user={}",
            path.name,
            len(data) // 1024,
            "общий кэш" if self.is_shared(size) else "персональный",
            user_id,
        )
        return path

    def cleanup(self, now: float | None = None) -> int:
        """Удалить просроченные файлы (TTL истёк). Сколько удалено."""
        removed = 0
        now = time.time() if now is None else now
        if not self._root.exists():
            return 0
        for path in self._root.rglob("*"):
            if path.is_file() and now - path.stat().st_mtime >= self._ttl:
                path.unlink(missing_ok=True)
                removed += 1
                logger.info("вложение удалено (TTL {}с): {}", int(self._ttl), path.name)
        # убрать опустевшие каталоги (сначала глубокие)
        for directory in sorted(
            (p for p in self._root.rglob("*") if p.is_dir()),
            key=lambda p: len(p.parts),
            reverse=True,
        ):
            try:
                directory.rmdir()
            except OSError:
                pass  # не пустой — ок
        return removed


async def cleanup_loop(
    cache: AttachmentCache, interval: float = 60.0
) -> None:
    """Фоновая уборка кэша: раз в interval секунд удаляются просроченные файлы.

    Отдельное событие на каждое вложение не создаётся: уборка — техническая
    задача, а не доменное событие; надёжнее периодический проход. Дополнительно
    каждый файл проверяется лениво при обращении (старше TTL = считается
    отсутствующим и скачивается заново).
    """
    while True:
        await asyncio.sleep(interval)
        try:
            removed = cache.cleanup()
        except Exception as error:
            logger.error("кэш вложений: ошибка уборки: {}", error)
            continue
        if removed:
            logger.info(
                "кэш вложений: удалено просроченных файлов: {}", removed
            )
