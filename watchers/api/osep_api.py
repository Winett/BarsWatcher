import asyncio
import json
from typing import Optional

import aiohttp
from loguru import logger

from watchers.api.base_api import BaseAPI
from watchers.auth.osep_auth import OsepAuth
from watchers.core.exceptions import ResponseError
from watchers.models.mail_models import Folders, ConversationNodes, Attachment, AttachmentData


class OsepAPI(BaseAPI):
    """API методы для ОСЭП (почта)"""

    BASE_URL = "https://mail.mpei.ru"

    def __init__(self, auth: OsepAuth):
        super().__init__(auth, self.BASE_URL)
        self._root_folder_id: str | None = None

    def headers_to_update(self, action: str):
        return {
            "Action": action,
            "X-Owa-Canary": self.auth.x_owa_canary,
            "x-requested-with": "XMLHttpRequest",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 OPR/125.0.0.0 (Edition Yx GX)",
            "referer": "https://mail.mpei.ru/owa/",
        }

    async def get_root_folder_id(self) -> str:
        if self._root_folder_id:
            return self._root_folder_id
        headers = self.headers_to_update("GetOwaUserConfiguration")
        answer = await self._request_with_authorization(
            endpoint="/owa/service.svc?action=GetOwaUserConfiguration",
            method="POST",
            headers=headers,
        )
        answer = json.loads(answer)
        session_settings = answer.get("SessionSettings", {})
        folder_names = session_settings.get("DefaultFolderNames", [])
        ind = folder_names.index("msgfolderroot")
        self._root_folder_id = session_settings.get("DefaultFolderIds", [])[ind].get("Id", "")
        return self._root_folder_id

    async def get_folders(self) -> Folders:
        root_folder_id = await self.get_root_folder_id()

        data = {
            "__type": "FindFolderJsonRequest:#Exchange",
            "Header": {
                "__type": "JsonRequestHeaders:#Exchange",
                "RequestServerVersion": "Exchange2013",
                "TimeZoneContext": {
                    "__type": "TimeZoneContext:#Exchange",
                    "TimeZoneDefinition": {
                        "__type": "TimeZoneDefinitionType:#Exchange",
                        "Id": "Russian Standard Time"
                    }
                }
            },
            "Body": {
                "__type": "FindFolderRequest:#Exchange",
                "FolderShape": {
                    "__type": "FolderResponseShape:#Exchange",
                    "BaseShape": "IdOnly"
                },
                "Paging": {
                    "__type": "IndexedPageView:#Exchange",
                    "BasePoint": "Beginning",
                    "Offset": 0,
                    "MaxEntriesReturned": 10000
                },
                "ParentFolderIds": [
                    {
                        "__type": "FolderId:#Exchange",
                        "ChangeKey": "AQAAAA==",
                        "Id": root_folder_id
                    }
                ],
                "Traversal": "Deep",
                "ShapeName": "Folder",
                "ReturnParentFolder": True,
                "RequiredFolders": [
                    "inbox"
                ],
                "FoldersToMoveToTop": None
            }
        }
        folders = await self._request_with_authorization(
            endpoint="/owa/service.svc",
            method="POST",
            params={"action": "FindFolder"},
            headers=self.headers_to_update("FindFolder"),
            json=data,
        )
        folders = json.loads(folders)
        folders = folders.get("Body", {}).get("ResponseMessages", {}).get("Items", [{}])[0].get("RootFolder", {})
        return Folders(**folders)

    async def get_conversation_items(self, conversation_id: str) -> ConversationNodes:
        data = {
            "__type": "GetConversationItemsJsonRequest:#Exchange",
            "Header": {
                "__type": "JsonRequestHeaders:#Exchange",
                "RequestServerVersion": "Exchange2013",
                "TimeZoneContext": {
                    "__type": "TimeZoneContext:#Exchange",
                    "TimeZoneDefinition": {
                        "__type": "TimeZoneDefinitionType:#Exchange",
                        "Id": "Russian Standard Time"
                    }
                }
            },
            "Body": {
                "__type": "GetConversationItemsRequest:#Exchange",
                "Conversations": [
                    {
                        "__type": "ConversationRequestType:#Exchange",
                        "ConversationId": {
                            "__type": "ItemId:#Exchange",
                            "Id": conversation_id
                        }
                    }
                ],
                "ItemShape": {
                    "__type": "ItemResponseShape:#Exchange",
                    "BaseShape": "IdOnly",
                    "BodyType": "Text",
                    "FilterHtmlContent": True
                },
                "ShapeName": "ItemPartUniqueBody",
                "SortOrder": "DateOrderDescending",
            }
        }
        items = await self._request_with_authorization(
            endpoint="/owa/service.svc",
            method="POST",
            params={"action": "GetConversationItems"},
            headers=self.headers_to_update("GetConversationItems"),
            json=data,
        )
        items = json.loads(items)
        items = items.get("Body", {}).get("ResponseMessages", {}).get("Items", [{}])[0].get(
            "Conversation", {"ConversationNodes": []}
        )
        return ConversationNodes(**items)

    async def load_attachment(self, attachment: Attachment) -> bytes:
        params = {
            "id": attachment.attachment_id.id,
            "X-OWA-CANARY": self.auth.x_owa_canary
        }
        return await self._request_with_authorization(
            "/owa/service.svc/s/GetFileAttachment",
            method="GET",
            params=params
        )

    async def find_conversations_from_folder(
        self,
        folder_id: str = "inbox",
        type_folder_id: str = "DistinguishedFolderId",
        max_entries_returned: int = 5
    ):
        payload = {
            "__type": "FindConversationJsonRequest:#Exchange",
            "Header": {
                "__type": "JsonRequestHeaders:#Exchange",
                "RequestServerVersion": "Exchange2013",
                "TimeZoneContext": {
                    "__type": "TimeZoneContext:#Exchange",
                    "TimeZoneDefinition": {
                        "__type": "TimeZoneDefinitionType:#Exchange",
                        "Id": "Russian Standard Time"
                    }
                }
            },
            "Body": {
                "__type": "FindConversationRequest:#Exchange",
                "ParentFolderId": {
                    "__type": "TargetFolderId:#Exchange",
                    "BaseFolderId": {
                        "__type": f"{type_folder_id}:#Exchange",
                        "Id": folder_id
                    }
                },
                "ConversationShape": {
                    "__type": "ConversationResponseShape:#Exchange",
                    "BaseShape": "IdOnly"
                },
                "ShapeName": "ConversationListView",
                "Paging": {
                    "__type": "IndexedPageView:#Exchange",
                    "BasePoint": "Beginning",
                    "Offset": 0,
                    "MaxEntriesReturned": max_entries_returned
                },
                "ViewFilter": "All",
                "SortOrder": [
                    {
                        "__type": "SortResults:#Exchange",
                        "Order": "Descending",
                        "Path": {
                            "__type": "PropertyUri:#Exchange",
                            "FieldURI": "ConversationLastDeliveryTime"
                        }
                    }
                ]
            }
        }
        headers = self.headers_to_update("FindConversation")
        response = await self._request_with_authorization(
            endpoint="/owa/service.svc?action=FindConversation",
            method="POST",
            json=payload,
            headers=headers
        )
        try:
            response = json.loads(response.decode('utf-8', errors='ignore'))
            return response
        except json.JSONDecodeError:
            raise ResponseError(message="Ошибка декодирования из json", content=response)
