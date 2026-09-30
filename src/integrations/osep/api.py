"""Запросы OWA JSON API (/owa/service.svc) с авто-relogin.

Все тела — JSON `*JsonRequest:#Exchange` (Exchange 2013). Канарейка
X-OWA-CANARY передаётся заголовком X-Owa-Canary; для вложения — ещё и
query-параметром. 302/401 = сессия слетела → перелогин и один повтор.
"""

from __future__ import annotations

import httpx
from loguru import logger

from src.integrations.ports import (
    AuthError,
    DataParseError,
    ServiceUnavailableError,
)

from .auth import OwaAuth

__all__ = ["OwaApi", "ATTACHMENT_LIMIT_BYTES"]

SERVICE_SVC = "/owa/service.svc"
ATTACHMENT_LIMIT_BYTES = 100 * 1024 * 1024  # как в master: лимит 100 МБ

# общий заголовок каждого запроса (RequestServerVersion + часовой пояс)
_HEADERS = {
    "__type": "JsonRequestHeaders:#Exchange",
    "RequestServerVersion": "Exchange2013",
    "TimeZoneContext": {
        "__type": "TimeZoneContext:#Exchange",
        "TimeZoneDefinition": {
            "__type": "TimeZoneDefinitionType:#Exchange",
            "Id": "Russian Standard Time",
        },
    },
}


class OwaApi:
    """Низкоуровневые запросы OWA поверх сессии OwaAuth."""

    def __init__(self, auth: OwaAuth):
        self._auth = auth

    def _headers(self, action: str) -> dict:
        headers = {
            "Action": action,
            "x-requested-with": "XMLHttpRequest",
            "referer": "https://mail.mpei.ru/owa/",
        }
        canary = self._auth.canary
        if canary:
            headers["X-Owa-Canary"] = canary
        return headers

    async def _post(self, action: str, payload: dict | None) -> dict:
        """POST service.svc; при 302/401 — перелогин и один повтор."""
        for attempt in (1, 2):
            try:
                response = await self._auth.client.post(
                    SERVICE_SVC,
                    params={"action": action},
                    json=payload,
                    headers=self._headers(action),
                )
            except httpx.HTTPError as exc:
                raise ServiceUnavailableError(
                    f"ОСЭП: {action} не прошёл, {exc!r}"
                ) from exc

            if response.status_code in (302, 401):
                if attempt == 1 and self._auth.can_relogin():
                    logger.warning("ОСЭП: сессия истекла ({}) — перелогин", action)
                    await self._auth.relogin()
                    continue
                raise AuthError(f"ОСЭП: сессия истекла ({action})")

            if response.status_code >= 500 or response.status_code == 429:
                raise ServiceUnavailableError(
                    f"ОСЭП: {action} → HTTP {response.status_code}"
                )

            try:
                return response.json()
            except ValueError as exc:
                raise DataParseError(
                    f"ОСЭП: {action} вернул не JSON "
                    f"({len(response.content)} байт, HTTP {response.status_code})"
                ) from exc
        raise AuthError(f"ОСЭП: {action} не выполнился")  # недостижимо

    # ─────────────────────────── эндпоинты OWA ───────────────────────────

    async def get_user_configuration(self) -> dict:
        """Пустое тело → SessionSettings (DefaultFolderNames/Ids)."""
        return await self._post("GetOwaUserConfiguration", None)

    async def find_folders(self, root_folder_id: str) -> dict:
        """Дерево папок со счётчиками (Traversal: Deep от корня)."""
        return await self._post(
            "FindFolder",
            {
                "__type": "FindFolderJsonRequest:#Exchange",
                "Header": _HEADERS,
                "Body": {
                    "__type": "FindFolderRequest:#Exchange",
                    "FolderShape": {
                        "__type": "FolderResponseShape:#Exchange",
                        "BaseShape": "IdOnly",
                    },
                    "Paging": {
                        "__type": "IndexedPageView:#Exchange",
                        "BasePoint": "Beginning",
                        "Offset": 0,
                        "MaxEntriesReturned": 10000,
                    },
                    "ParentFolderIds": [
                        {
                            "__type": "FolderId:#Exchange",
                            "ChangeKey": "AQAAAA==",
                            "Id": root_folder_id,
                        }
                    ],
                    "Traversal": "Deep",
                    "ShapeName": "Folder",
                    "ReturnParentFolder": True,
                    "RequiredFolders": ["inbox"],
                    "FoldersToMoveToTop": None,
                },
            },
        )

    async def find_conversations(self, folder_id: str, limit: int) -> dict:
        """Треды папки от новых к старым (ConversationLastDeliveryTime)."""
        return await self._post(
            "FindConversation",
            {
                "__type": "FindConversationJsonRequest:#Exchange",
                "Header": _HEADERS,
                "Body": {
                    "__type": "FindConversationRequest:#Exchange",
                    "ParentFolderId": {
                        "__type": "TargetFolderId:#Exchange",
                        "BaseFolderId": {
                            "__type": "FolderId:#Exchange",
                            "Id": folder_id,
                        },
                    },
                    "ConversationShape": {
                        "__type": "ConversationResponseShape:#Exchange",
                        "BaseShape": "IdOnly",
                    },
                    "ShapeName": "ConversationListView",
                    "Paging": {
                        "__type": "IndexedPageView:#Exchange",
                        "BasePoint": "Beginning",
                        "Offset": 0,
                        "MaxEntriesReturned": limit,
                    },
                    "ViewFilter": "All",
                    "SortOrder": [
                        {
                            "__type": "SortResults:#Exchange",
                            "Order": "Descending",
                            "Path": {
                                "__type": "PropertyUri:#Exchange",
                                "FieldURI": "ConversationLastDeliveryTime",
                            },
                        }
                    ],
                },
            },
        )

    async def get_conversation_items(self, conversation_id: str) -> dict:
        """Письма треда (свежайшее — ConversationNodes[0].Items[0])."""
        return await self._post(
            "GetConversationItems",
            {
                "__type": "GetConversationItemsJsonRequest:#Exchange",
                "Header": _HEADERS,
                "Body": {
                    "__type": "GetConversationItemsRequest:#Exchange",
                    "Conversations": [
                        {
                            "__type": "ConversationRequestType:#Exchange",
                            "ConversationId": {
                                "__type": "ItemId:#Exchange",
                                "Id": conversation_id,
                            },
                        }
                    ],
                    "ItemShape": {
                        "__type": "ItemResponseShape:#Exchange",
                        "BaseShape": "IdOnly",
                        "BodyType": "Text",
                        "FilterHtmlContent": True,
                    },
                    "ShapeName": "ItemPartUniqueBody",
                    "SortOrder": "DateOrderDescending",
                },
            },
        )

    async def get_attachment_bytes(self, attachment_id: str) -> bytes:
        """Скачать вложение: GET GetFileAttachment → сырые байты (не base64)."""
        params: dict[str, str] = {"id": attachment_id}
        canary = self._auth.canary
        if canary:
            params["X-OWA-CANARY"] = canary

        for attempt in (1, 2):
            try:
                response = await self._auth.client.get(
                    "/owa/service.svc/s/GetFileAttachment",
                    params=params,
                    headers=self._headers("GetFileAttachment"),
                )
            except httpx.HTTPError as exc:
                raise ServiceUnavailableError(
                    f"ОСЭП: вложение не скачалось, {exc!r}"
                ) from exc

            if response.status_code in (302, 401):
                if attempt == 1 and self._auth.can_relogin():
                    await self._auth.relogin()
                    if self._auth.canary:
                        params["X-OWA-CANARY"] = self._auth.canary
                    continue
                raise AuthError("ОСЭП: сессия истекла (вложение)")

            if response.status_code >= 500 or response.status_code == 429:
                raise ServiceUnavailableError(
                    f"ОСЭП: вложение → HTTP {response.status_code}"
                )

            data = response.content
            if len(data) > ATTACHMENT_LIMIT_BYTES:
                raise DataParseError(
                    f"ОСЭП: вложение больше лимита "
                    f"{ATTACHMENT_LIMIT_BYTES // (1024 * 1024)} МБ"
                )
            return data
        raise AuthError("ОСЭП: вложение не скачалось")  # недостижимо
