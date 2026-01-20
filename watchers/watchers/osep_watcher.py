import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from loguru import logger

from watchers.managers.watcher_manager import OsepWatcherManager
from watchers.core.base_watcher import BaseWatcher
from watchers.fetchers.osep_fetcher import OsepFetcher
from watchers.models.watcher_models import WatcherType, WatcherEvent, EventType
from watchers.models.mail_models import MailMessage, AttachmentData, NewMailEvent, Folders, Folder

from hashlib import md5

import aiofiles
from pathlib import Path

from settings import WORKDIR



class OsepWatcher(BaseWatcher):
    def __init__(self, credentials, *args, **kwargs):
        super().__init__(credentials, *args, **kwargs)

        self.fetcher_service = OsepFetcher(credentials)
        original_login = self.fetcher_service.login

        async def wrapped_login(*args, **kwargs):
            self._stats.last_auth_time = datetime.now()
            return await original_login(*args, **kwargs)

        self.fetcher_service.login = wrapped_login

        origin__request_with_authorization = self.fetcher_service._request_with_authorization
        async def wrapped_request(*args, **kwargs):
            self._stats.last_fetch_time = datetime.now()
            return await origin__request_with_authorization(*args, **kwargs)

        self.fetcher_service._request_with_authorization = wrapped_request

        self.config.poll_interval = 0  # 5 минут для почты
        self._last_sync_token: Optional[str] = None
        self._folders_cache_key = f"osep_folders_{self.credentials.username}"

    def _register_instance(self):
        OsepWatcherManager.register_watcher(self.credentials.user_id, self)

    async def fetch_data(self) -> Dict:
        """Получение данных о папках и письмах"""

        async def _process_new_mail(conversation_id: str):
        # async def _process_new_mail(event: NewMailEvent):
            items = await self.fetcher_service.get_conversation_items(conversation_id)
            new_massage = items.conversation_nodes[0].items[0]

            load_data = []
            if new_massage.has_attachments:
                attachments = new_massage.attachments
                for attachment in attachments:
                    attachment_cache_key = f"attachment_osep_{attachment.attachment_id.id}"
                    # file = {
                    #         "content_type": "text/plain",
                    #         "filename": "27.txt",
                    #         "size": 170,
                    #         "id": "AAMkADBiMDM2ZmI3LTI0Y2ItNDMzMy05OWQ1LTRhY2Y0ZDFmYmNhNABGAAAAAAAXF5gPgvkQRbR8chVGnnQxBwBf9fplkHqsS5Ua52t4fVokAAAAAAEMAABf9fplkHqsS5Ua52t4fVokAAIbn3tDAAABEgAQABVQ1swmUVlKg0dswdVL/os=",
                    #         "content": None
                    #     }
                    file = await self.cache_service.get(attachment_cache_key)
                    if not file:
                        file_content = await self.fetcher_service.load_attachment(attachment)
                        att_data = AttachmentData(
                            id=attachment.attachment_id.id,
                            content_type=attachment.content_type,
                            filename=attachment.name,
                            size=attachment.size,
                            content=file_content
                        )

                        (WORKDIR / Path("cashed_files/")).mkdir(exist_ok=True)

                        path = WORKDIR / Path(f"cashed_files/{md5(att_data.id.encode(errors="ignore")).hexdigest()}_{att_data.filename}.bin")


                        async with aiofiles.open(path, "wb") as f:
                            await f.write(file_content)
                        await self.cache_service.set(
                            attachment_cache_key,
                        {
                            "content_type": attachment.content_type,
                            "filename": attachment.name,
                            "size": attachment.size,
                            "id": attachment.attachment_id.id,
                            "content": None
                        },
                            self.config.cache_file_ttl
                        )
                        load_data.append(att_data)
                    else:
                        att_data = AttachmentData(**file)
                        async with aiofiles.open(WORKDIR / f"cashed_files/{md5(att_data.id.encode(errors="ignore")).hexdigest()}_{att_data.filename}.bin", "rb") as f:
                            att_data.content = await f.read()
                        load_data.append(att_data)

            mail_message = MailMessage(
                conversation_id=new_massage.conversation_id.id,
                subject=new_massage.subject,
                sender_email=new_massage.from_.mail_box.email_address,
                sender_name=new_massage.from_.mail_box.name,
                body=new_massage.unique_body.value,
                has_attachments=new_massage.has_attachments,
                attachments=load_data
            )

            watcher_event = self._generator_events(
                event_type=EventType.NEW_CHANGE,
                message=mail_message.format_message(),

                files=load_data,
                mail_message=mail_message,
            )

            self.event_service.notify_subscribers(watcher_event)

        async def _update_folders_cache():
            folders = await self.fetcher_service.get_folders()
            self._stats.last_fetch_time = datetime.now()
            logger.debug(self._logger_template + f"Обновляю Кэш папок")
            await self.cache_service.set(self._folders_cache_key, {folder.display_name: folder for folder in folders.folders}, ttl=self.config.cache_ttl)
            logger.debug(self._logger_template + f'Сохранил кэш')

        def _process_new_mail_event(event: NewMailEvent):

            if event.id == "NewMailNotification":
                asyncio.create_task(_process_new_mail(event.conversation_id))
                asyncio.create_task(_update_folders_cache())
            else:
                logger.warning(f"Необычное событие при лонг поллинг: {event = }")

        folders = await self.fetcher_service.get_folders()
        self._stats.last_fetch_time = datetime.now()

        old_folders = await self.cache_service.get(self._folders_cache_key)

        if old_folders:
            old_folders = {folder_name: Folder(**old_folders[folder_name]) for folder_name in old_folders.keys()}
            folders = {folder.display_name: folder for folder in folders.folders}
            ignore_folders = {"Журнал", "Задачи", "Заметки", "Исходящие", "Нежелательная почта", "Отправленные",
                              "Удаленные", "Черновики",
                              "Conversation Action Settings", "Working Set", "{06967759-274D-40B2-A3EB-D7F9E73727D7}",
                              "{A9E2BC46-B3A0-4243-B315-60D991004455}",
                              "GAL Contacts", "Recipient Cache"}
            for folder_name, old_folder in old_folders.items():

                new_folder: Folder = folders.get(folder_name)
                if not old_folder or not new_folder or folder_name in ignore_folders:
                    continue

                if old_folder.total_count < new_folder.total_count:
                    conversations = await self.fetcher_service.find_conversations_from_folder(
                        folder_id=new_folder.folder_id.id,
                        type_folder_id="FolderId",
                        max_entries_returned=new_folder.total_count - old_folder.total_count
                    )
                    conversations = conversations.get("Body", {}).get("Conversations", [])

                    for conversation in conversations:
                        conv_id = conversation.get("ConversationId", {}).get("Id")
                        if not conv_id:
                            logger.warning(f"Не удалось обработать новое письмо во время пропуска")
                        asyncio.create_task(_process_new_mail(conv_id))

        await _update_folders_cache()


        await self.fetcher_service.await_new_messages(_process_new_mail_event)
        self._stats.last_fetch_time = datetime.now()

        await _update_folders_cache()


    async def process_data(self, data: Dict) -> List[MailMessage]:
        """Обработка почтовых данных"""
        pass


    async def detect_changes(self, old_data: List[MailMessage],
                             new_data: List[MailMessage]) -> List[str]:
        """Обнаружение новых писем"""
        pass



    async def _notify_changes(self, changes: List[str], **metadata):
        """Переопределение для отправки уведомлений с вложениями"""
        for change in changes:
            event = WatcherEvent(
                event_type=EventType.NEW_CHANGE,
                user_id=self.credentials.user_id,
                username=self.credentials.username,
                status=self._stats.status,
                watcher_type=WatcherType.OSEP,
                message=change,
                metadata=metadata
            )

            await self._notify_subscribers(event)

    async def close(self):
        await self.fetcher_service.close()

