from loguru import logger

from .auth.osep import OsepAuth
from .base import BaseWatcher
from .cacher import BaseCacher, FileCacher
from .connection.base import OsepConnectionMonitor
from .fetcher.osep_fetcher import OsepFetcher

from watchers.parser.base import OsepNotificatorEvent, AttachmentData
import asyncio

from .notificator.base import BaseNotificator
from .notificator.model import NotificatorMessage, WatcherType
from watchers.parser.base import Attachment

from .models.folder import MailFolder, MailsFolders


class OsepWatcher(BaseWatcher):

    def __init__(self, username, password, user_id, notifier: BaseNotificator, casher: BaseCacher = FileCacher()):
        super().__init__(username, password, user_id, auth_class=OsepAuth, fetcher_class=OsepFetcher, connection_class=OsepConnectionMonitor)
        self.notifier = notifier
        self.casher = casher
        self._template_cache = f"{self.__class__.__name__}_{self.username}"

        self._config.poll_interval = 0

    async def test_login(self):
        result = await self.auth.login()
        return result

    @staticmethod
    def _message_builder(message: str, from_: str, from_name: str, subject: str, has_attachments: bool = False) -> str:
        base_message = (f"Новое письмо!\n\n"
                        f"От: {from_name}\n"
                        f"Тема: {subject}\n\n"
                        f"{message}")

        if has_attachments:
            return base_message + "\n\nЕсть вложения!"
        return base_message

    async def notify(self, message: str, attachments_data: list[AttachmentData] = None):
        logger.debug(f"Отправка нового письма ({self.username}); Вложения: {bool(attachments_data)}")
        files: list[bytes] = []
        attachments = []
        try:
            if attachments_data:
                files = await self.fetcher.load_attachments(attachments_data)

            for content, file in zip(files, attachments_data):
                attachments.append(Attachment(content=content, filename=file.filename))
        except Exception as e:
            logger.error(f"Ошибка загрузки вложений: {e}")

        message = NotificatorMessage(message=message, user_id=self.user_id, files=attachments, watcher=WatcherType.OSEP)
        await self.notifier.notify(message)


    async def _process_event_new_message(self, conversation_id: str) -> None:
        # conversation_id = event.ConversationId
        response: dict = await self.fetcher.get_conversation_items(conversation_id)

        body = response.get('Body')
        if not body:
            logger.warning(self._logger_template + f"При получении нового письма не пришёл Body")
            return

        response_messages = body.get('ResponseMessages')
        if not response_messages:
            logger.warning(self._logger_template + f"При получении нового письма не пришёл ResponseMessages")
            return

        items = response_messages.get('Items')
        if not items:
            logger.warning(self._logger_template + f"При получении нового письма не пришёл Items")
            return
        if len(items) > 1:
            logger.debug(self._logger_template + f"Необычное количество сообщений в Items: {len(items)}; {items}")

        conversation = items[0].get("Conversation")
        if not conversation:
            logger.warning(self._logger_template + f"При получении нового письма не пришёл Conversation")
            return

        total_conversation_nodes_count = conversation.get('TotalConvesationNodesCount')
        nodes: list = conversation.get('ConversationNodes')
        if not nodes:
            logger.warning(self._logger_template + f"При получении нового письма не пришёл ConversationNodes")
            return

        new_message_items = nodes[0].get('Items')
        if not new_message_items:
            logger.warning(self._logger_template + f"При получении нового письма не пришёл Items")

        new_message = new_message_items[0]
        has_attachments = new_message.get('HasAttachments', False)
        attachments: list[AttachmentData] = []
        if has_attachments:
            attachments_data = new_message.get('Attachments')
            for attachment_data in attachments_data:
                attachments.append(AttachmentData(id=attachment_data.get('AttachmentId', {}).get('Id'), content_type=attachment_data.get('ContentType'), filename=attachment_data.get('Name'), size=attachment_data.get('Size')))


        message = new_message.get('UniqueBody', {}).get('Value')
        from_ = new_message.get('From', {}).get('Mailbox', {}).get('EmailAddress')
        from_name = new_message.get('From', {}).get('Mailbox', {}).get('Name')
        subject = new_message.get('Subject')

        message_to_send = self._message_builder(message, from_, from_name, subject, has_attachments)
        await self.notify(message_to_send, attachments)



    def _callback(self, events: list[OsepNotificatorEvent]):
        for event in events:
            if event.id == "NewMailNotification":
                # logger.debug(f"Получено новое письмо: {event}")
                asyncio.create_task(self._process_event_new_message(event.ConversationId))

    async def _get_skipped_message_and_send_it(self, folder_id: str, max_entries_returned: int = 1):
        request = await self.fetcher.find_conversations_from_folder(folder_id=folder_id, type_folder_id="FolderId", max_entries_returned=max_entries_returned)

        body = request.get('Body')
        if not body:
            return

        conversations = body.get('Conversations', [])
        new_messages = []
        for conv in conversations:
            conv_id = conv.get("ConversationId", {}).get("Id")
            if not conv_id:
                logger.warning(self._logger_template + f"Не удалось получить Id письма")
                continue
            new_messages.append(asyncio.create_task(self._process_event_new_message(conv_id)))

        result = await asyncio.gather(*new_messages, return_exceptions=True)
        additional_message = "Ошибок не произошло" if not any(errors := list(isinstance(r, Exception) for r in result)) else "Произли ошибки при отправке "
        logger.info(self._logger_template + f"Успешно было обработыно {len(result) - errors.count(True)}/{len(result)} писем " + additional_message)

    @staticmethod
    def _serialize_find_folder(data: dict) -> MailsFolders:
        body: dict = data.get('Body')
        if not body:
            logger.warning(f"Не найдено Body при проверке на пропущенные письма")
            return MailsFolders()

        response_messages: dict = body.get('ResponseMessages')
        if not response_messages:
            logger.warning(f"Не найдено ResponseMessages при проверке на пропущенные письма")
            return MailsFolders()

        items = response_messages.get('Items')
        if not items:
            logger.warning(f"Не найдено Items при проверке на пропущенные письма")
            return MailsFolders()

        root_folder = items[0].get('RootFolder')

        folders_to_ignore = ['Recipient Cache', 'Нежелательная почта', 'Отправленные', 'Удаленные', 'Черновики',
                             'Корневой уровень хранилища']

        folders = root_folder.get('Folders', [])
        my_folders = {}

        for folder in folders:
            if not (display_name := folder.get('DisplayName')) or display_name in folders_to_ignore:
                continue

            folder_id = folder.get('FolderId', {}).get('Id')
            total_count_messages = folder.get('TotalCount', 0)
            my_folder = MailFolder(folder_id=folder_id, display_name=display_name,
                                   total_count_messages=total_count_messages)
            my_folders[display_name] = my_folder

        folders = MailsFolders(folders=my_folders)
        return folders



    def _checked_skipped_mail(self, new_data: dict):
        last_data: MailsFolders = self.casher.get(self._template_cache, MailsFolders, multiple=False)

        folders = self._serialize_find_folder(new_data)

        if not last_data:
            self.casher.set(self._template_cache, folders)
            return

        args = []

        for folder_name in folders.folders.keys():
            old_folder = last_data.folders.get(folder_name)
            new_folder = folders.folders.get(folder_name)
            if not old_folder:
                continue
            if new_folder.total_count_messages > old_folder.total_count_messages:
                logger.info(self._logger_template + f"Найдено новое сообщение, пока сервер не оправшивался, в папке '{folder_name}'")
                args.append((new_folder.folder_id, new_folder.total_count_messages - old_folder.total_count_messages))

        self.casher.set(self._template_cache, folders)
        for arg in args:
            asyncio.create_task(self._get_skipped_message_and_send_it(*arg))



    async def _fetch_and_process_data(self):

        logger.debug(self._logger_template + "Проверка папок на пропущенные письма")
        folders = await self.fetcher.find_folders()
        self._checked_skipped_mail(folders)

        logger.debug(self._logger_template + "Начало Лонг Полинга")
        await self.fetcher.long_polling_test(self._callback)

        logger.debug(self._logger_template + "Сохранение текущих состояний писем в папках")
        folders = await self.fetcher.find_folders()
        self.casher.set(self._template_cache, self._serialize_find_folder(folders))
