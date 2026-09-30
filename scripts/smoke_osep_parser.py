"""Smoke-проверки ОСЭП: разбор JSON OWA + кэш вложений (без сети и БД).

  uv run python scripts/smoke_osep_parser.py
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("SECRET_KEY", "smoke")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:smoke")
os.environ.setdefault("ADMINS", "[1]")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", "postgres")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "barswatcher")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.integrations.osep.parser import (  # noqa: E402
    extract_root_folder_id,
    parse_conversation_ids,
    parse_folders,
    parse_mail_item,
)
from src.services.attachments import AttachmentCache  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    if condition:
        print(f"[OK ] {label}")
    else:
        failures += 1
        print(f"[FAIL] {label}")


# ─────────────────────────── разбор JSON OWA ───────────────────────────

CONFIG = {
    "SessionSettings": {
        "DefaultFolderNames": ["inbox", "msgfolderroot", "notes"],
        "DefaultFolderIds": [
            {"Id": "ID-INBOX"},
            {"Id": "ID-ROOT"},
            {"Id": "ID-NOTES"},
        ],
    }
}

FIND_FOLDERS = {
    "Body": {
        "ResponseMessages": {
            "Items": [
                {
                    "RootFolder": {
                        "Folders": [
                            {
                                "DisplayName": "Входящие",
                                "FolderId": {
                                    "Id": "AAMkAGI2...",
                                    "ChangeKey": "EQAAAB...",
                                },
                                "TotalCount": 230,
                                "UnreadCount": 5,
                            },
                            {
                                "DisplayName": "Удаленные",
                                "FolderId": {"Id": "AAMkAGI9..."},
                                "TotalCount": 7,
                                "UnreadCount": 0,
                            },
                        ]
                    }
                }
            ]
        }
    }
}

FIND_CONVERSATIONS = {
    "Body": {
        "Conversations": [
            {"ConversationId": {"Id": "CONV-1"}},
            {"ConversationId": {"Id": "CONV-2"}},
        ]
    }
}

CONVERSATION_ITEMS = {
    "Body": {
        "ResponseMessages": {
            "Items": [
                {
                    "Conversation": {
                        "ConversationNodes": [
                            {
                                "Items": [
                                    {
                                        "Subject": "Допуск к зачёту",
                                        "From": {
                                            "Mailbox": {
                                                "Name": "Учебный отдел",
                                                "EmailAddress": "study@mpei.ru",
                                            }
                                        },
                                        "UniqueBody": {
                                            "Value": "Добрый день! См. вложение."
                                        },
                                        "HasAttachments": True,
                                        "Attachments": [
                                            {
                                                "AttachmentId": {"Id": "ATT-1"},
                                                "Name": "dopusk.pdf",
                                                "ContentType": "application/pdf",
                                                "Size": 182340,
                                            }
                                        ],
                                    }
                                ]
                            }
                        ]
                    }
                }
            ]
        }
    }
}


def run_parser_checks() -> None:
    check(extract_root_folder_id(CONFIG) == "ID-ROOT", "id msgfolderroot")

    folders = parse_folders(FIND_FOLDERS)
    check(set(folders) == {"Входящие", "Удаленные"}, "папки: имена")
    inbox = folders.get("Входящие")
    check(
        inbox is not None
        and inbox.folder_id == "AAMkAGI2..."
        and inbox.total_count == 230
        and inbox.unread_count == 5,
        "папка «Входящие»: id/total/unread",
    )

    check(
        parse_conversation_ids(FIND_CONVERSATIONS) == ["CONV-1", "CONV-2"],
        "список тредов (от новых к старым)",
    )

    mail = parse_mail_item(CONVERSATION_ITEMS)
    check(mail is not None and mail.subject == "Допуск к зачёту", "тема письма")
    check(
        mail is not None
        and mail.sender_name == "Учебный отдел"
        and mail.sender_email == "study@mpei.ru",
        "отправитель",
    )
    check(mail is not None and mail.body.startswith("Добрый день!"), "тело (UniqueBody)")
    check(mail is not None and mail.has_attachments, "флаг вложений")
    check(
        mail is not None
        and len(mail.attachments) == 1
        and mail.attachments[0].filename == "dopusk.pdf"
        and mail.attachments[0].attachment_id == "ATT-1"
        and mail.attachments[0].size == 182340,
        "вложение: имя/id/размер",
    )


# ─────────────────────────── кэш вложений ───────────────────────────


def run_cache_checks() -> None:
    root = tempfile.mkdtemp(prefix="osep-cache-")
    cache = AttachmentCache(root=root, ttl_seconds=600)
    calls: list[str] = []

    async def download(tag: str, payload: bytes):
        async def _inner() -> bytes:
            calls.append(tag)
            return payload

        return _inner

    async def main() -> None:
        # 1. файл < 1 КБ: персональный кэш — каждый пользователь качает свой
        for user in (1, 2):
            await cache.get_or_download(
                attachment_id="small-1",
                filename="rasch.txt",
                size=512,
                user_id=user,
                download=await download(f"user{user}", b"x" * 512),
            )
        check(calls == ["user1", "user2"], "<1КБ: per-user (два скачивания)")

        # 2. файл >= 1 КБ: общий кэш — второй пользователь не качает
        calls.clear()
        for user in (1, 2):
            await cache.get_or_download(
                attachment_id="big-1",
                filename="dopusk.pdf",
                size=182340,
                user_id=user,
                download=await download(f"user{user}", b"y" * 4096),
            )
        check(calls == ["user1"], ">=1КБ: общий кэш (одно скачивание)")

        # 3. повторное прикрепление — сразу из кэша
        calls.clear()
        path = await cache.get_or_download(
            attachment_id="big-1",
            filename="dopusk.pdf",
            size=182340,
            user_id=1,
            download=await download("again", b"y" * 4096),
        )
        check(calls == [] and path.exists(), "повтор: файл прикрепляется без скачивания")

        # 4. TTL: просроченные файлы удаляются
        removed = cache.cleanup(now=path.stat().st_mtime + 601)
        check(removed >= 1 and not path.exists(), "TTL: просроченные файлы удалены")

    asyncio.run(main())
    shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    print("=== разбор JSON OWA ===")
    run_parser_checks()
    print("\n=== кэш вложений ===")
    run_cache_checks()
    print()
    if failures:
        print(f"ОСЭП: smoke ПРОВАЛЕН ({failures}) ❌")
        sys.exit(1)
    print("ОСЭП: smoke пройден ✅")
