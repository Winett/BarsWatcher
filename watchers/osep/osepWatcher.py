from asyncio import sleep
import json

from aiohttp import ConnectionTimeoutError, ClientSession, ClientTimeout
from loguru import logger

from bs4 import BeautifulSoup

from watchers.base import BaseAuth
from watchers.exceptions import LoginError, ServerError500, ServerError401
from watchers.osep.osepmodel import AttachmentData
from settings import settings

class WatcherOsep(BaseAuth):
    mails_url = 'https://mail.mpei.ru/owa/sessiondata.ashx?appcacheclient=0'
    login_url = 'https://mail.mpei.ru/CookieAuth.dll?Logon'
    owa_url = 'https://mail.mpei.ru/owa/#path=/mail'
    attachments_url = "https://mail.mpei.ru/owa/service.svc?action=GetConversationItems"
    get_attachment_url = "https://mail.mpei.ru/owa/service.svc/s/GetFileAttachment"
    find_conversation_url = "https://mail.mpei.ru/owa/service.svc?action=FindConversation" #URL для получения писем

    if settings.DEBUG:
        timeout = 5
    else:
        timeout = 60

    def __init__(self, username: str, password: str):
        super().__init__(username, password)
        self.watching = False

    async def login(self) -> bool:
        session = await self.get_session()
        if await self._load_session() and await self.check_auth(session):
            logger.info(f"-- Авторизация с помощью cookies({self.username}) --")
            return True
        session.cookie_jar.clear()
        async with session.post(self.login_url, data={'curl': 'Z2FowaZ2F', 'flags': 0, 'forcedownlevel': 0, 'formdir': 2, 'username': self.username, 'password': self.password, 'isUtf8': 1, 'trusted': 4}, allow_redirects=False):
            pass
        if await self.check_auth(session):
            logger.info(f"{self.__class__.__name__}-- Авторизация с помощью пароля({self.username}) --")
            self._session = session
            await self._save_session()
            return True

        logger.warning(f"{self.__class__.__name__}-- Ошибка авторизации ({self.username})--")
        raise LoginError(f"Неверные данные для входа: {self.username}")

    async def check_auth(self, session) -> bool:
        async with session.post(self.mails_url, allow_redirects=False) as response:
            return response.status == 200

    def stop(self):
        self.watching = False
        self._cleanup()

    @staticmethod
    def generate_get_conversation_items_payload(conversation_id):
        body = {
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
                        },
                        "SyncState": ""
                    }
                ],
                "ItemShape": {
                    "__type": "ItemResponseShape:#Exchange",
                    "BaseShape": "IdOnly",
                    "FilterHtmlContent": True,
                    "BlockExternalImagesIfSenderUntrusted": True,
                    "AddBlankTargetToLinks": True,
                    "ClientSupportsIrm": True,
                    "InlineImageUrlTemplate": "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAEALAAAAAABAAEAAAIBTAA7",
                    "MaximumBodySize": 2097152,
                    "MaximumRecipientsToReturn": 10,
                    "CssScopeClassName": "rps_861b",
                    "InlineImageUrlOnLoadTemplate": "InlineImageLoader.GetLoader().Load(this)",
                    "InlineImageCustomDataTemplate": "{id}"
                },
                "ShapeName": "ItemPartUniqueBody",
                "SortOrder": "DateOrderDescending",
                "MaxItemsToReturn": 20
            }
        }

        return body

    @staticmethod
    def generate_find_conversation_payload():
        body = {
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
                    "__type": "DistinguishedFolderId:#Exchange",
                    "Id": "inbox"
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
                  "MaxEntriesReturned": 200
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
        return body

    async def get_conversation_items(self, url: str, json_payload: dict):
        session = await self.get_session()
        session.headers.update(
            {'Action': 'GetConversationItems', 'X-OWA-CANARY':
                session.cookie_jar.filter_cookies(url).get('X-OWA-CANARY').value})
        response: dict = await self._make_request(url, method='POST', json=json_payload)
        return response

    async def get_attachment_ids(self, conversation_id) -> list:
        data = await self.get_conversation_items(self.attachments_url, self.generate_get_conversation_items_payload(conversation_id))
        attachments = []
        for attachment in data['Body']['ResponseMessages']['Items'][0]['Conversation']['ConversationNodes'][0]['Items'][0]['Attachments']:
            attachments.append(AttachmentData(**attachment))
        logger.debug(f"Получены ID вложений ({self.username})")
        return attachments

    async def get_attachment(self, attachment_id):
        session = await self.get_session()
        X_OWA_CANARY = session.cookie_jar.filter_cookies(self.attachments_url).get('X-OWA-CANARY').value
        response = await self._make_request(self.get_attachment_url, method='GET', params={'id': attachment_id, 'X-OWA-CANARY': X_OWA_CANARY})
        return response


    async def _make_request(self, url: str, method: str = 'GET', **kwargs):
        session = await self.get_session()
        attempts = 0
        max_attempts = 3
        while attempts < max_attempts:
            attempts += 1
            try:
                async with session.request(
                        method,
                        url,
                        **kwargs
                ) as response:

                    if response.status == 401:
                        logger.warning(f"Сессия устарела, выполняем перелогин ({self.username})")
                        await self.login()
                        continue

                    if response.status == 500:
                        logger.error(f"Ошибка сервера 500 ({self.username})")
                        raise ServerError500(f"Ошибка сервера при запросе к {url}")

                    if 'application/json' in response.headers.get('Content-Type', ''):
                        return await response.json()
                    return await response.read()

            except Exception as e:
                if attempts == max_attempts:
                    logger.error(f"Не удалось выполнить запрос после {max_attempts} попыток ({self.username}): {e}")
                    raise
                await sleep(1)

    async def _get_conversations(self, session):
        session.headers.update({
            'Action': 'FindConversation',
            'X-OWA-CANARY': session.cookie_jar.filter_cookies(self.find_conversation_url).get('X-OWA-CANARY').value
        })

        async with session.post(
                self.find_conversation_url,
                allow_redirects=False,
                json=self.generate_find_conversation_payload()
        ) as response:
            if response.status == 401:
                logger.warning(f"-- Сессия устарела, выполняем перелогин ({self.username}) --")
                await self.login()
                await sleep(5)
                return await self._get_conversations(session)

            if response.status == 500:
                logger.warning(f"-- Ошибка сервера ({self.username}) --")
                raise ServerError500("-- Ошибка сервера --")

            try:
                return await response.json()
            except json.decoder.JSONDecodeError:
                logger.error(
                    f"{self.__class__.__name__} Ошибка декодирования JSON ({self.username}): {await response.text()}")
                await sleep(5)
                return await self._get_conversations(session)

    async def _format_message(self, conv):
        msg = (f"НОВОЕ ПИСЬМО!\n"
               f"От: {', '.join(conv['UniqueSenders'])}\n"
               f"Тема: {conv['ConversationTopic']}\n\n")

        if len(conv['Preview']) >= 230:
            data = await self.get_conversation_items(
                self.attachments_url,
                self.generate_get_conversation_items_payload(conv['ConversationId']['Id'])
            )
            txt = data['Body']['ResponseMessages']['Items'][0]['Conversation']['ConversationNodes'][0]['Items'][0][
                'UniqueBody']['Value']
            txt = await self._process_html_content(txt)
            msg += txt
        else:
            msg += conv['Preview']
        return msg

    @staticmethod
    async def _process_html_content(html_content):
        soup = BeautifulSoup(html_content, 'html.parser')
        txt = soup.text.strip()

        for a in soup.find_all('a'):
            attrs = a.attrs
            link_text = a.get_text(strip=True)
            attrs_str = ' '.join([f'{k}="{v}"' for k, v in attrs.items()])
            new_a_tag = f'<a {attrs_str}>{link_text}</a>'
            txt = txt.replace(a.text, new_a_tag, 1)

        return txt

    async def _handle_message_error(self, error, conv, callback):
        error_msg = f"Ошибка отправки письма ({self.username}): {conv}\n{error.__class__.__name__} {error}"
        logger.error(error_msg)
        await callback("Пришло новое письмо, но произошли ошибки при отправке; Проверьте ОСЭП")
        await callback(error_msg, user_id=settings.admins[0])

    @logger.catch(reraise=True)
    async def watch(self, callback):
        self.watching = True
        last_conversations = {}
        last_total_conversations = 0

        async def process_new_messages(current_conversations, new_message_ids):
            for conv_id in new_message_ids:
                conv = current_conversations[conv_id]
                try:
                    msg = await self._format_message(conv)
                    files = []
                    if conv['HasAttachments']:
                        files = await self.get_attachment_ids(conv['ConversationId']['Id'])
                        msg += "\nЕсть вложения!"
                    logger.debug(f"Отправка нового письма пользователю {self.username}")
                    await callback(msg, files=files)
                    logger.debug(f"Письмо пользователю {self.username} успешно отправлено")
                except Exception as e:
                    await self._handle_message_error(e, conv, callback)

        session = await self.get_session()

        while self.watching:
            try:
                if session.closed:
                    logger.warning(f"-- Сессия закрылась, пеероткрываю ({self.username}) --")
                    session = await self.get_session()
                    continue

                current_data = await self._get_conversations(session)
                current_conversations = {
                    conv['ConversationId']['Id']: conv
                    for conv in current_data['Body']['Conversations']
                }
                if not last_conversations:
                    last_conversations = current_conversations
                    last_total_conversations = current_data['Body']['TotalConversationsInView']
                    logger.info(
                        f"{self.__class__.__name__} Инициализация: сохранено текущее состояние писем ({self.username})")
                    await sleep(self.timeout)
                    continue
                if last_total_conversations < current_data['Body']['TotalConversationsInView']:
                    new_message_ids = current_conversations.keys() - last_conversations.keys()
                    await process_new_messages(current_conversations, new_message_ids)
                last_total_conversations = current_data['Body']['TotalConversationsInView']
                last_conversations = current_conversations
            except LoginError:
                logger.warning(f"Ошибка авторизации: {self.username}")
                raise
            except ServerError500 as e:
                logger.error(f"{self.__class__.__name__} Ошибка сервера ({self.username}): {e}")
                await callback(f"Ошибка сервера ({self.username}): {e}", user_id=settings.admins[0])
                raise
            except ConnectionTimeoutError:
                logger.error(f"{self.__class__.__name__} Ошибка соединения TimeoutError ({self.username})")
                await callback(f"Ошибка соединения ({self.username})", user_id=settings.admins[0])
                raise
            except Exception as e:
                logger.error(f"{self.__class__.__name__} Ошибка в основном цикле watch ({self.username}): {e}")
                await callback(f"Ошибка в основном цикле watch ({self.username}): {e}", user_id=settings.admins[0])
                await sleep(10)
            finally:
                await sleep(self.timeout)
