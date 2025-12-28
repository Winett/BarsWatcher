import json
from .base import BaseFetcher
from loguru import logger
import asyncio
from watchers.parser.base import AttachmentData


from watchers.parser.base import OsepParserLongPolling, OsepNotificatorEvent
from typing import Callable

class OsepFetcher(BaseFetcher):
    async def fetch(self, url: str, **kwargs) -> bytes | str | dict:
        answer, response = await self.fetch_raw(url, **kwargs)

        if response.status != 200:
            logger.warning(self._logger_template + f"Необычный статус ответа {url}: {response.status}")

        if response.content_type != "application/json":
            return answer

        return json.loads(answer)

    @staticmethod
    def _get_owa_canary(session):
        a = session.cookie_jar.filter_cookies(
                "https://mail.mpei.ru/owa/service.svc?action=FindFolder").get('X-OWA-CANARY')
        return a.value if a else "."

    async def _update_headers(self, action: str):
        session = await self.session_provider()
        new_headers = {
            'Action': action,
            "X-OWA-CANARY": self._get_owa_canary(session)
        }
        session.headers.update(new_headers)


    async def find_folders(self):
        await self._update_headers("FindFolder")
        payload: dict = self._find_folder_payload()


        response = await self.fetch("https://mail.mpei.ru/owa/service.svc?action=FindFolder", method="POST", json=payload)
        return response

    @staticmethod
    def _find_conversation_payload(folder_id: str, type_folder_id: str = "DistinguishedFolderId", max_entries_returned: int = 5, offset: int = 0):
        #type_folder_id: DistinguishedFolderId => folder_id = "inbox", "spam"...
        #type_folder_id: FolderId => folder_id = "AAMkADBi..."
        return {
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
                        "Offset": offset,
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

    @staticmethod
    def _find_folder_payload():
        return {
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

    async def find_conversations_from_folder(self, folder_id: str = "inbox", type_folder_id: str = "DistinguishedFolderId",max_entries_returned: int = 5, offset: int = 0):
        await self._update_headers("FindConversation")
        payload: dict = self._find_conversation_payload(folder_id=folder_id, type_folder_id=type_folder_id, max_entries_returned=max_entries_returned, offset=offset)
        response = await self.fetch("https://mail.mpei.ru/owa/service.svc?action=FindConversation", method="POST", json=payload)
        return response
    @staticmethod
    def _get_conversation_items_payload(conversation_id: str):
        return {
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


    async def get_conversation_items(self, conversation_id: str):
        await self._update_headers("GetConversationItems")
        payload = self._get_conversation_items_payload(conversation_id=conversation_id)
        response = await self.fetch("https://mail.mpei.ru/owa/service.svc?action=GetConversationItems", method="POST", json=payload)
        return response

    async def load_attachment(self, attachment_id: str) -> bytes:
        session = await self.session_provider()
        params = {
            "id": attachment_id,
            "X-OWA-CANARY":self._get_owa_canary(session)
        }
        response = await self.fetch("https://mail.mpei.ru/owa/service.svc/s/GetFileAttachment", method="GET", params=params)
        return response

    async def load_attachments(self, attachments: list[AttachmentData]):
        # tasks = [self.load_attachment(attachment.id) for attachment in attachments]
        # return await asyncio.gather(*tasks)
        results = []
        for attachment in attachments:
            results.append(await self.load_attachment(attachment.id))
        return results

    async def long_polling_test(self, callback: Callable[[list[OsepNotificatorEvent]], None]):
        from uuid import uuid4
        session = await self.session_provider()
        uid = str(uuid4())
        await self._update_headers("SubscribeToNotification")
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
        async with session.post("https://mail.mpei.ru/owa/service.svc?action=SubscribeToNotification", json=payload) as response:
            logger.debug(f"Попытка подключения лонг поллинга")
            ans = await response.json()
            logger.debug(f"{ans = }")

        logger.debug(f"Попытка прослушивания лонг полинга")

        async with session.get(f"https://mail.mpei.ru/owa/ev.owa2?ns=PendingRequest&ev=PendingNotificationRequest&UA=0&cid="
                               f"{uid}&brwnm=chrome&X-OWA-CANARY={self._get_owa_canary(session)}&n=96", timeout=600) as response:
            logger.debug(f"Начало получения уведомлений")
            logger.debug(f"{response.status = }")

            async for chunk in response.content.iter_chunked(1024 * 1024):
                if chunk:
                    html_chunk = chunk.decode('utf-8', errors='ignore')
                    parse_obj = OsepParserLongPolling(html_chunk)
                    new_event = parse_obj.parse()
                    if new_event:
                        callback(new_event)
