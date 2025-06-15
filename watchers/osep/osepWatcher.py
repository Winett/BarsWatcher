from asyncio import sleep
import json
from loguru import logger

from watchers.base import BaseAuth
from watchers.exceptions import LoginError
from watchers.osep.osepmodel import AttachmentData
from settings import settings

class WatcherOsep(BaseAuth):
    mails_url = 'https://mail.mpei.ru/owa/sessiondata.ashx?appcacheclient=0'
    login_url = 'https://mail.mpei.ru/CookieAuth.dll?Logon'
    owa_url = 'https://mail.mpei.ru/owa/#path=/mail'
    attachments_url = "https://mail.mpei.ru/owa/service.svc?action=GetConversationItems"
    get_attachment_url = "https://mail.mpei.ru/owa/service.svc/s/GetFileAttachment"

    if settings.DEBUG:
        timeout = 5
    else:
        timeout = 60

    def __init__(self, username: str, password: str):
        super().__init__(username, password)
        self.watching = False

    async def login(self) -> bool:
        session = await self.get_session()
        async with session:
            if await self._load_session() and await self.check_auth(session):
                logger.info(f"-- Авторизация с помощью cookies({self.username}) --")
                return True
            session.cookie_jar.clear()
            async with session.post(self.login_url, data={'curl': 'Z2FowaZ2F', 'flags': 0, 'forcedownlevel': 0, 'formdir': 2, 'username': self.username, 'password': self.password, 'isUtf8': 1, 'trusted': 4}, allow_redirects=False):
                pass
            if await self.check_auth(session):
                logger.info(f"{self.__class__.__name__}-- Авторизация с помощью пароля({self.username}) --")
                await self._save_session()
                return True

            logger.warning(f"{self.__class__.__name__}-- Ошибка авторизации --")
            raise LoginError(f"Неверные данные для входа: {self.username}")

    async def check_auth(self, session) -> bool:
        async with session.post(self.mails_url, allow_redirects=False) as response:
            return response.status == 200

    def stop(self):
        self.watching = False

    @logger.catch
    async def watch(self, callback):

        self.watching = True
        last_conversations = {}
        session = await self.get_session()
        async with session:
            while self.watching:
                if session.closed:
                    logger.warning("-- Сессия закрылась, пеероткрываю --")
                    session = await self.get_session()
                try:
                    async with session.post(self.mails_url, allow_redirects=False) as response:
                        # logger.debug(f"{self.__class__.__name__} Проверка авторизации: {session.cookie_jar.filter_cookies(self.mails_url)}")
                        if response.status == 401:
                            logger.warning("-- Сессия устарела, выполняем перелогин --")
                            await self.login()
                            await sleep(5)
                            continue

                        try:
                            current_data = json.loads(await response.text())
                        except json.decoder.JSONDecodeError:
                            logger.error(f"{self.__class__.__name__} Ошибка декодирования JSON: {(await response.text())}")
                            await sleep(5)
                            continue

                    current_conversations = {
                        conv['ConversationId']['Id']: conv
                        for conv in current_data['findConversation']['Body']['Conversations']
                    }

                    if not last_conversations:
                        last_conversations = current_conversations
                        logger.info(f"{self.__class__.__name__} Инициализация: сохранено текущее состояние писем")
                        await sleep(self.timeout)
                        continue

                    new_messages = []
                    updated_messages = []

                    for conv_id in set(current_conversations.keys()) - set(last_conversations.keys()):
                        new_messages.append(current_conversations[conv_id])

                    for conv in new_messages:
                        msg = (f"НОВОЕ ПИСЬМО!\n"
                                 f"От: {', '.join(conv['UniqueSenders'])}\n"
                                 f"Тема: {conv['ConversationTopic']}\n"
                                 f"Содержание: {conv['Preview']}\n\n")
                        files = []
                        if conv['HasAttachments']:
                            files = await self.get_attachment_ids(conv['ConversationId']['Id'])
                            msg += f"Есть вложения!"

                        await callback(msg, files=files)


                    for conv in updated_messages:
                        logger.info(f"ОБНОВЛЕНО ПИСЬМО: {conv['ConversationTopic']}")

                    last_conversations = current_conversations

                except Exception as e:
                    logger.error(f"{self.__class__.__name__} Ошибка в основном цикле watch: {e.__class__.__name__} {e.args} {e}")
                    await callback(f"Ошибка в основном цикле watch: {e.__class__.__name__} {e.args} {e}")
                    await sleep(10)

                finally:
                    await sleep(self.timeout)

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

    async def get_attachment_ids(self, conversation_id) -> list:
        session = await self.get_session()
        async with session:
            session.headers.update(
                {'Action': 'GetConversationItems', 'X-OWA-CANARY':
                    session.cookie_jar.filter_cookies(self.attachments_url).get('X-OWA-CANARY').value})
            async with session.post(self.attachments_url,
                                    json=self.generate_get_conversation_items_payload(conversation_id)
                                    ) as response:
                data = await response.json()
            attachments = []
            for attachment in data['Body']['ResponseMessages']['Items'][0]['Conversation']['ConversationNodes'][0]['Items'][0]['Attachments']:
                attachments.append(AttachmentData(**attachment))
            return attachments

    async def get_attachment(self, attachment_id):
        session = await self.get_session()
        async with session:
            X_OWA_CANARY = session.cookie_jar.filter_cookies(self.attachments_url).get('X-OWA-CANARY').value
            async with session.get(self.get_attachment_url, params={'id': attachment_id, 'X-OWA-CANARY': X_OWA_CANARY}) as response:
                return await response.read()