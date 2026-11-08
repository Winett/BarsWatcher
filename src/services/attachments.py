"""Кэш вложений ОСЭП на диске.

Вложение скачивается один раз и живёт ограниченное время (TTL), затем
удаляется. Если файл уже есть в кэше — он прикрепляется сразу, без повторного
скачивания. Конкурентные запросы одного файла сходятся в single-flight:
скачивает один, остальные берут готовое.

ПРИВАТНОСТЬ. «Расчётные листки» по стипендии содержат личные данные студента
и не должны попадать другим пользователям. Совместный кэш (один скачал —
все прикрепляют) применяется только когда выполнены ОБА условия:
  - файл >= 1.5 КБ (личные листки обычно меньше; запас до 1.5 КБ);
  - есть настоящий attachment_id — он уникален для каждого письма, поэтому
    чужой личный файл не может попасть в ту же запись.
Файлы без attachment_id (ключом стало бы имя файла — оно совпадает у разных
студентов) всегда персональные, как и файлы < 1.5 КБ.
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
        shared_min_size: int = 1536,
    ):
        self._root = Path(root)
        self._ttl = ttl_seconds
        self._shared_min = shared_min_size
        self._locks: dict[str, asyncio.Lock] = {}

    def _entry_dir(
        self, *, attachment_id: str, filename: str, size: int, user_id: int
    ) -> Path:
        """Каталог записи: общий только для безопасных случаев (см. докстринг)."""
        shared = self.is_shared(size) and bool(attachment_id)
        key = _safe_key(attachment_id or filename)
        if shared:
            return self._root / "shared" / key
        return self._root / f"user-{user_id}" / key

    def is_shared(self, size: int) -> bool:
        """Размер позволяет кэшировать совместно (>= 1.5 КБ)."""
        return size >= self._shared_min

    def _fresh(self, path: Path) -> bool:
        return path.exists() and (time.time() - path.stat().st_mtime) < self._ttl

    def _lock_for(self, entry: Path) -> asyncio.Lock:
        """Одна загрузка на файл: конкурентные запросы дожидаются скачанного."""
        key = str(entry)
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    async def get_or_download(
        self,
        *,
        attachment_id: str,
        filename: str,
        size: int,
        user_id: int,
        download: Downloader,
    ) -> Path:
        """Путь к файлу: из кэша, либо скачанный и сохранённый в кэш.

        Конкурентные запросы одного файла скачивают его один раз (single-flight):
        пока первый качает, остальные ждут и берут готовый файл.
        """
        entry = self._entry_dir(
            attachment_id=attachment_id, filename=filename, size=size, user_id=user_id
        )
        path = entry / _safe_name(filename)
        if self._fresh(path):
            logger.info(
                "📂 Вложение {} загружено из кэша | user={}",
                path.name, user_id,
            )
            return path

        async with self._lock_for(entry):
            if self._fresh(path):
                # пока ждали очереди, файл уже скачали — не качаем повторно
                logger.info(
                    "📂 Вложение {} загружено из кэша (дождались скачивания) | user={}",
                    path.name, user_id,
                )
                return path
            data = await download()
            entry.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        logger.info(
            "✅ Вложение {} скачано на диск | {} КБ | {} | user={}",
            path.name,
            len(data) // 1024,
            "общий кэш" if self.is_shared(size) and attachment_id else "персональный",
            user_id,
        )
        return path

    def get_if_cached(
        self,
        *,
        attachment_id: str,
        filename: str,
        size: int,
        user_id: int,
    ) -> Path | None:
        """Вернуть путь к файлу если он уже в кэше (и не просрочен), иначе None.

        Используется в notifier чтобы НЕ скачивать повторно то, что уже
        положил worker — иначе двойное скачивание и двойное удаление в логах.
        """
        entry = self._entry_dir(
            attachment_id=attachment_id, filename=filename, size=size, user_id=user_id
        )
        path = entry / _safe_name(filename)
        if self._fresh(path):
            logger.info(
                "📂 Вложение {} загружено из кэша | user={}",
                path.name, user_id,
            )
            return path
        return None

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
                logger.info("🗑 Вложение {} удалено (устарело, TTL {}с)", path.name, int(self._ttl))
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
        # блокировки удалённых файлов больше не нужны
        self._locks = {k: v for k, v in self._locks.items() if Path(k).exists()}
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
