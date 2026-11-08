"""Тест безопасности отправки вложений: файлы не попадают «не тем».

Симулирует реальный путь: worker кладёт вложение в кэш (как в
_prefetch_attachments), бот читает его (как в _send_attachments) — с
asdict-сериализацией события между ними.
"""

import asyncio
import shutil
import sys
import tempfile
from dataclasses import asdict

sys.path.insert(0, ".")

from src.integrations.models import Attachment
from src.services.attachments import AttachmentCache


def bot_side_get(attachment: dict):
    """Ключи ровно как в notifier._send_attachments."""
    return (
        str(attachment.get("attachment_id") or ""),
        str(attachment.get("filename") or "file"),
        int(attachment.get("size") or 0),
    )


async def main() -> None:
    root = tempfile.mkdtemp(prefix="send-safety-")
    cache = AttachmentCache(root=root, ttl_seconds=600)
    downloads: list[str] = []

    def downloader(tag: str, payload: bytes):
        async def _inner() -> bytes:
            downloads.append(tag)
            return payload

        return _inner

    # ── 1. Расчётные листки: одинаковое имя, разное содержимое, < 1.5 КБ ──
    u1_slip = Attachment(filename="Расчётный листок.txt", size=1200, attachment_id="att-100")
    u2_slip = Attachment(filename="Расчётный листок.txt", size=1200, attachment_id="att-200")
    # worker кладёт (как _prefetch_attachments: сырой id)
    await cache.get_or_download(
        attachment_id=u1_slip.attachment_id, filename=u1_slip.filename,
        size=u1_slip.size, user_id=1,
        download=downloader("slip-u1", b"LEAVE-PAYSLIP-USER-1"),
    )
    await cache.get_or_download(
        attachment_id=u2_slip.attachment_id, filename=u2_slip.filename,
        size=u2_slip.size, user_id=2,
        download=downloader("slip-u2", b"LEAVE-PAYSLIP-USER-2"),
    )
    # бот читает (как _send_attachments: asdict-словарь из события)
    got = {}
    for user, att in ((1, u1_slip), (2, u2_slip)):
        aid, fname, size = bot_side_get(asdict(att))
        p = cache.get_if_cached(attachment_id=aid, filename=fname, size=size, user_id=user)
        assert p is not None, (user, att)
        got[user] = p.read_bytes()
    assert got[1] == b"LEAVE-PAYSLIP-USER-1", got
    assert got[2] == b"LEAVE-PAYSLIP-USER-2", got
    print("[OK] листки: каждый получил СОЙ файл, не чужой")

    # ── 2. Общий файл (>= 1.5 КБ, с id): качается один раз, оба берут копию ──
    shared = Attachment(filename="dopusk.pdf", size=182340, attachment_id="att-shared")
    await cache.get_or_download(
        attachment_id=shared.attachment_id, filename=shared.filename,
        size=shared.size, user_id=1,
        download=downloader("shared", b"SAME-CONTENT"),
    )
    p2 = await cache.get_or_download(
        attachment_id=shared.attachment_id, filename=shared.filename,
        size=shared.size, user_id=2,
        download=downloader("shared-dup", b"SAME-CONTENT"),
    )
    assert "shared-dup" not in downloads, downloads
    assert p2.read_bytes() == b"SAME-CONTENT"
    print("[OK] общий файл: одно скачивание, второму — копия из кэша")

    # ── 3. Файл БЕЗ id с одинаковым именем у двух студентов (опасный случай!) ──
    noid1 = Attachment(filename="Расчётный листок.pdf", size=182340, attachment_id="")
    noid2 = Attachment(filename="Расчётный листок.pdf", size=182340, attachment_id="")
    await cache.get_or_download(
        attachment_id=noid1.attachment_id, filename=noid1.filename,
        size=noid1.size, user_id=1,
        download=downloader("noid-u1", b"STUDENT-1-DATA"),
    )
    await cache.get_or_download(
        attachment_id=noid2.attachment_id, filename=noid2.filename,
        size=noid2.size, user_id=2,
        download=downloader("noid-u2", b"STUDENT-2-DATA"),
    )
    got = {}
    for user, att in ((1, noid1), (2, noid2)):
        aid, fname, size = bot_side_get(asdict(att))
        p = cache.get_if_cached(attachment_id=aid, filename=fname, size=size, user_id=user)
        assert p is not None
        got[user] = p.read_bytes()
    assert got[1] == b"STUDENT-1-DATA" and got[2] == b"STUDENT-2-DATA", got
    print("[OK] без id: одинаковое имя НЕ шарится, каждый получил своё")

    # ── 4. Гонка worker-префетч против бота: одно скачивание ──
    downloads.clear()
    race = Attachment(filename="race.bin", size=182340, attachment_id="att-race")
    await asyncio.gather(
        cache.get_or_download(
            attachment_id=race.attachment_id, filename=race.filename,
            size=race.size, user_id=1,
            download=downloader("race", b"RACE"),
        ),
        cache.get_or_download(
            attachment_id=race.attachment_id, filename=race.filename,
            size=race.size, user_id=1,
            download=downloader("race-dup", b"RACE"),
        ),
    )
    assert downloads == ["race"], downloads
    print("[OK] гонка: одно скачивание")

    shutil.rmtree(root, ignore_errors=True)
    print("ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")


asyncio.run(main())