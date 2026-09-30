"""Клиент ОСЭП (OWA) — реализация порта OsepClient.

Склейка OwaAuth (вход, канарейка) + OwaApi (service.svc) + parser.
Письма ищутся по росту счётчика TotalCount папки (как в master):
fetch_folder_counts() для снимка, fetch_new_mails(folder, count) —
свежайшие треды папки.
"""

from __future__ import annotations

from loguru import logger

from src.integrations.models import MailMessage
from src.integrations.ports import AuthResult, DataParseError

from .api import OwaApi
from .auth import OwaAuth
from .parser import (
    extract_root_folder_id,
    parse_conversation_ids,
    parse_folders,
    parse_mail_item,
)

__all__ = ["OsepWebClient"]


class OsepWebClient:
    """Клиент ОСЭП: авторизация, папки, новые письма, вложения."""

    def __init__(self, timeout: float = 30.0):
        self._auth = OwaAuth(timeout=timeout)
        self._api = OwaApi(self._auth)
        self._root_folder_id: str | None = None

    async def is_authenticated(self) -> bool:
        return await self._auth.is_authenticated()

    async def authenticate(self, login: str, password: str) -> AuthResult:
        self._root_folder_id = None
        return await self._auth.authenticate(login, password)

    # ────────────────────────────── данные ──────────────────────────────

    async def _folders(self) -> dict:
        if not self._root_folder_id:
            config = await self._api.get_user_configuration()
            self._root_folder_id = extract_root_folder_id(config)
            if not self._root_folder_id:
                raise DataParseError(
                    "ОСЭП: не найден msgfolderroot в GetOwaUserConfiguration"
                )
        find = await self._api.find_folders(self._root_folder_id)
        return parse_folders(find)

    async def fetch_folder_counts(self) -> dict[str, int]:
        """{имя папки: TotalCount} — по этим счётчикам детектятся новые письма."""
        folders = await self._folders()
        return {name: folder.total_count for name, folder in folders.items()}

    async def fetch_new_mails(self, folder: str, count: int) -> list[MailMessage]:
        """Свежайшие письма папки (count штук, от новых к старым)."""
        if count <= 0:
            return []
        folders = await self._folders()
        info = folders.get(folder)
        if info is None or not info.folder_id:
            logger.warning("ОСЭП: папка «{}» не найдена", folder)
            return []

        find = await self._api.find_conversations(info.folder_id, count)
        mails: list[MailMessage] = []
        for conversation_id in parse_conversation_ids(find)[:count]:
            items = await self._api.get_conversation_items(conversation_id)
            mail = parse_mail_item(items)
            if mail is not None:
                mails.append(mail)
        return mails

    async def download_attachment(self, attachment_id: str) -> bytes:
        """Скачать вложение целиком (сырые байты)."""
        return await self._api.get_attachment_bytes(attachment_id)

    async def check_availability(self) -> bool:
        return await self._auth.check_availability()

    # ────────────────────────────── сессии ──────────────────────────────

    def export_session(self) -> dict:
        return self._auth.export_session()

    def restore_session(self, session: dict | None) -> None:
        self._auth.restore_session(session)
