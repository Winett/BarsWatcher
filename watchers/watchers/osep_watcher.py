import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from hashlib import md5
from pathlib import Path

import aiofiles
from loguru import logger

from watchers.core.base_watcher import BaseWatcher
from watchers.core.event_service import EventService
from watchers.models.watcher_models import UserCredentials, WatcherConfig, WatcherType, EventType
from watchers.models.mail_models import (
    MailMessage, AttachmentData, Folder, Folders, NewMailEvent
)
from watchers.services.cache_service import AsyncFileCacher
from watchers.api.osep_api import OsepAPI
from watchers.managers.watcher_manager import OsepWatcherManager
from settings import WORKDIR


class OsepWatcher(BaseWatcher):
    def __init__(
        self,
        credentials: UserCredentials,
        api: OsepAPI,
        cache_service: AsyncFileCacher,
        config: Optional[WatcherConfig] = None,
    ):
        super().__init__(credentials, cache_service, config)
        self.api = api
        self.config.poll_interval = 60
        self._folders_cache_key = f"osep_folders_{self.credentials.username}"

    def _register_instance(self):
        OsepWatcherManager.register_watcher(self.credentials.user_id, self)
        logger.debug(f"{self._logger_template} Зарегистрирован в OsepWatcherManager")

    async def fetch_data(self) -> Dict:
        """Один снимок: получить текущее состояние папок и вернуть их."""
        logger.debug(f"{self._logger_template} osep_api.get_folders()...")
        folders = await self.api.get_folders()
        self._stats.last_fetch_time = datetime.now()
        result = {folder.display_name: folder for folder in folders.folders}
        logger.debug(f"{self._logger_template} osep_api.get_folders() OK | папок: {len(result)}")
        return result

    async def process_data(self, data: Dict) -> Dict:
        """Обработка: вернуть данные как есть (словарь папок)"""
        logger.debug(f"{self._logger_template} process_data: папок={len(data)}")
        return data

    async def detect_changes(self, old_data: Dict, new_data: Dict) -> List[str]:
        """Обнаружение новых писем через сравнение count'ов папок"""
        if not old_data or not new_data:
            logger.debug(f"{self._logger_template} detect_changes: нет данных для сравнения")
            return []

        ignore_folders = {
            "Журнал", "Задачи", "Заметки", "Исходящие", "Нежелательная почта",
            "Отправленные", "Удаленные", "Черновики",
            "Conversation Action Settings", "Working Set",
            "{06967759-274D-40B2-A3EB-D7F9E73727D7}",
            "{A9E2BC46-B3A0-4243-B315-60D991004455}",
            "GAL Contacts", "Recipient Cache"
        }

        changes = []
        new_folders = {name: Folder(**data) if isinstance(data, dict) else data
                       for name, data in new_data.items()}

        for folder_name, old_folder in old_data.items():
            if folder_name in ignore_folders:
                continue

            new_folder = new_folders.get(folder_name)
            if not new_folder:
                continue

            old_count = old_folder.get('total_count', 0) if isinstance(old_folder, dict) else old_folder.total_count
            new_count = new_folder.total_count

            if old_count < new_count:
                diff = new_count - old_count
                logger.info(f"{self._logger_template} Новых писем в '{folder_name}': +{diff} (было {old_count}, стало {new_count})")
                await self._process_new_folder_mail(
                    folder_id=new_folder.folder_id.id,
                    new_count=diff
                )

        logger.debug(f"{self._logger_template} detect_changes: {len(changes)} изменений")
        return changes

    async def _process_new_folder_mail(self, folder_id: str, new_count: int):
        """Обработка новых писем в папке"""
        logger.debug(f"{self._logger_template} Поиск conversations в папке {folder_id}...")
        try:
            conversations = await self.api.find_conversations_from_folder(
                folder_id=folder_id,
                type_folder_id="FolderId",
                max_entries_returned=new_count
            )
            conversations = conversations.get("Body", {}).get("Conversations", [])
            logger.debug(f"{self._logger_template} Найдено conversations: {len(conversations)}")

            for conversation in conversations:
                conv_id = conversation.get("ConversationId", {}).get("Id")
                if conv_id:
                    logger.debug(f"{self._logger_template} Запуск обработки conversation {conv_id}")
                    asyncio.create_task(self._process_new_mail(conv_id))
        except Exception as e:
            logger.error(f"{self._logger_template} Ошибка обработки папки {folder_id}: {e}")

    async def _process_new_mail(self, conversation_id: str):
        """Обработка одного нового письма"""
        logger.debug(f"{self._logger_template} Обработка conversation {conversation_id}...")
        try:
            items = await self.api.get_conversation_items(conversation_id)
            new_message = items.conversation_nodes[0].items[0]

            logger.debug(
                f"{self._logger_template} Письмо: от={new_message.from_.mail_box.name} "
                f"тема={new_message.subject[:50]}..."
            )

            load_data = []
            if new_message.has_attachments:
                logger.debug(f"{self._logger_template} Вложений: {len(new_message.attachments)}")
                for attachment in new_message.attachments:
                    attachment_cache_key = f"attachment_osep_{attachment.attachment_id.id}"
                    file = await self.cache_service.get(attachment_cache_key)

                    if not file:
                        logger.debug(f"{self._logger_template} Скачивание вложения {attachment.name} ({attachment.size} bytes)...")
                        file_content = await self.api.load_attachment(attachment)
                        att_data = AttachmentData(
                            id=attachment.attachment_id.id,
                            content_type=attachment.content_type,
                            filename=attachment.name,
                            size=attachment.size,
                            content=file_content
                        )

                        (WORKDIR / Path("cashed_files/")).mkdir(exist_ok=True)
                        path = WORKDIR / Path(
                            f"cashed_files/{md5(att_data.id.encode(errors='ignore')).hexdigest()}_{att_data.filename}.bin"
                        )
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
                        logger.debug(f"{self._logger_template} Вложение {attachment.name} скачано и закэшировано")
                    else:
                        att_data = AttachmentData(**file)
                        path = WORKDIR / f"cashed_files/{md5(att_data.id.encode(errors='ignore')).hexdigest()}_{att_data.filename}.bin"
                        async with aiofiles.open(path, "rb") as f:
                            att_data.content = await f.read()
                        load_data.append(att_data)
                        logger.debug(f"{self._logger_template} Вложение {attachment.name} загружено из кэша")

            mail_message = MailMessage(
                conversation_id=new_message.conversation_id.id,
                subject=new_message.subject,
                sender_email=new_message.from_.mail_box.email_address,
                sender_name=new_message.from_.mail_box.name,
                body=new_message.unique_body.value,
                has_attachments=new_message.has_attachments,
                attachments=load_data
            )

            watcher_event = self._generator_events(
                event_type=EventType.NEW_CHANGE,
                message=mail_message.format_message(),
                files=load_data,
                mail_message=mail_message,
            )
            logger.info(f"{self._logger_template} Отправка уведомления о письме от {mail_message.sender_name}")
            EventService().notify_subscribers(watcher_event)

        except Exception as e:
            logger.error(f"{self._logger_template} Ошибка обработки conversation {conversation_id}: {e}")

    async def close(self):
        from watchers.session.pool_session import PoolSession
        logger.debug(f"{self._logger_template} Закрытие сессии...")
        await PoolSession.release(self.credentials.user_id, "osep")
        logger.debug(f"{self._logger_template} Сессия закрыта")
