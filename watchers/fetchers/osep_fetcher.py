import asyncio
import json
from typing import Callable

import aiohttp
from uuid import uuid4

from loguru import logger

from watchers.connectors.osep_connector import OsepConnector
from watchers.models.watcher_models import UserCredentials
from watchers.models.mail_models import Folders, ConversationNodes, Attachment, Conversation, AttachmentData, NewMailEvent
from bs4 import BeautifulSoup

from watchers.utils.decorators import retry
from watchers.utils.exceptions import ResponseError

class OsepFetcher(OsepConnector):
    def __init__(self, credentials: UserCredentials, timeout: int = 30):
        super().__init__(credentials, "https://mail.mpei.ru", timeout)


    @property
    def x_owa_canary(self):
        a = self.session.cookie_jar.filter_cookies(self.base_url).get('X-OWA-CANARY', "")
        return a.value if a else a

    async def fetch(self, *args, **kwargs):

        if kwargs.get("headers", {}).get("X-OWA-CANARY", " ") != self.x_owa_canary:
            kwargs.get("headers", {})["X-OWA-CANARY"] = self.x_owa_canary

        await super().fetch(*args, **kwargs)
        # async with session.get(self.base_url + '/owa/#path=/mail', allow_redirects=False) as response:
        #     pass


    def headers_to_update(self, action: str):
        return {
            "Action": action,
            "X-OWA-CANARY": self.x_owa_canary,
        }

    async def get_folders(self) -> Folders:
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
                    "Id": "AAMkADBiMDM2ZmI3LTI0Y2ItNDMzMy05OWQ1LTRhY2Y0ZDFmYmNhNAAuAAAAAAAXF5gPgvkQRbR8chVGnnQxAQBf9fplkHqsS5Ua52t4fVokAAAAAAEIAAA="
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
            params={
                "action": "FindFolder"
            },
            headers=self.headers_to_update("FindFolder"),
            json=data,
        )
        folders = json.loads(folders)
        folders = folders.get("Body", {}).get("ResponseMessages", {}).get("Items", [{}])[0].get("RootFolder",{})
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
            params={
                "action": "GetConversationItems"
            },
            headers=self.headers_to_update("GetConversationItems"),
            json=data,
        )
        items = json.loads(items)
        items = items.get("Body", {}).get("ResponseMessages", {}).get("Items", [{}])[0].get("Conversation",{"ConversationNodes": []})
        return ConversationNodes(**items)

    async def load_attachment(self, attachment: Attachment):
        params = {
            "id": attachment.attachment_id.id,
            "X-OWA-CANARY": self.x_owa_canary
        }

        response = await self._request_with_authorization("/owa/service.svc/s/GetFileAttachment", method="GET",
                                    params=params)
        return response

    async def load_attachments_from_conversation(self, conversation: Conversation) -> list[AttachmentData]:
        attachments = conversation.attachments
        results = []
        for attachment in attachments:
            try:
                file = await self.load_attachment(attachment)
            except (aiohttp.ClientConnectorError, asyncio.TimeoutError, aiohttp.ClientError) as e:
                file = None
            results.append(AttachmentData(id=attachment.attachment_id.id, content_type=attachment.content_type, filename=attachment.name, size=attachment.size, content=file))
        return results

    async def await_new_messages(self, callback_for_new_messages: Callable[[NewMailEvent], None]):
        uid = str(uuid4())

        headers = self.headers_to_update("SubscribeToNotification")
        payload = {
            "request": {
                "__type": "NotificationSubscribeJsonRequest:#Exchange",
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
                }
            },
            "subscriptionData": [
                {
                    "__type": "SubscriptionData:#Exchange",
                    "SubscriptionId": "NewMailNotification",
                    "Parameters": {
                        "__type": "SubscriptionParameters:#Exchange",
                        "NotificationType": "NewMailNotification",
                        "subscriptionIdSuffix": ""
                    }
                }
            ]
        }
        await self._request_with_authorization(endpoint="/owa/service.svc?action=SubscribeToNotification", method="POST", headers=headers, json=payload)

        def parse_html_chunks(content: str) -> NewMailEvent | None:
            soup = BeautifulSoup(content, "html.parser")
            for script in soup.find_all('script'):
                if script.text.startswith("{id:") or not script.text:
                    #Служебная информация
                    continue
                try:
                    data = json.loads(script.text)
                    return NewMailEvent(**data[0])
                except json.JSONDecodeError as e:
                    return NewMailEvent()

            return None

        try:
            while True:
                async with (self.session.get(
                        f"https://mail.mpei.ru/owa/ev.owa2?ns=PendingRequest&ev=PendingNotificationRequest&UA=0&cid="
                        f"{uid}&brwnm=chrome&X-OWA-CANARY={self.x_owa_canary}&n=96",
                        timeout=600)
                as response):
                    logger.debug(f"Начало лонг поллинга")


                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        if chunk:
                            html_chunk = chunk.decode('utf-8', errors='ignore')
                            new_event = parse_html_chunks(html_chunk)
                            if new_event:
                                callback_for_new_messages(new_event)
                    logger.debug(f"Конец лонг поллинга")

                await self._request_with_authorization(
                    f"/owa/ev.owa2?ns=PendingRequest&ev=FinishNotificationRequest&UA=0&cid={uid}",
                    method='POST',
                    headers={"X-OWA-CANARY": f"{self.x_owa_canary}"},
                    timeout=10)
        except asyncio.CancelledError:
            await self._request_with_authorization(
                f"/owa/ev.owa2?ns=PendingRequest&ev=FinishNotificationRequest&UA=0&cid={uid}",
                method='POST',
                headers={"X-OWA-CANARY": f"{self.x_owa_canary}"},
                timeout=10)

    async def find_conversations_from_folder(self, folder_id: str = "inbox", type_folder_id: str = "DistinguishedFolderId",max_entries_returned: int = 5):
        '''

        :param folder_id:
        :param type_folder_id: "DistinguishedFolderId" или "FolderId"
        :param max_entries_returned:
        :return:
        '''

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
        response = await self._request_with_authorization(endpoint="/owa/service.svc?action=FindConversation", method="POST", json=payload, headers=headers)
        try:
            response = json.loads(response.decode('utf-8', errors='ignore'))
            return response
        except json.JSONDecodeError:
            raise ResponseError(message="Ошибка декодирования из json", content=response)



