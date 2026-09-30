"""Разбор JSON-ответов OWA (Exchange 2013, /owa/service.svc).

Никакого SOAP/XML — OWA JSON API: тела запросов `*JsonRequest:#Exchange`,
ответы — те же структуры. Здесь только чистые функции разбора.
"""

from __future__ import annotations

from src.integrations.models import Attachment, MailMessage, OsepFolder

__all__ = [
    "extract_root_folder_id",
    "parse_folders",
    "parse_conversation_ids",
    "parse_mail_item",
]


def extract_root_folder_id(config: dict) -> str | None:
    """GetOwaUserConfiguration → id «msgfolderroot» (корень дерева папок)."""
    settings_ = config.get("SessionSettings") or {}
    names = settings_.get("DefaultFolderNames") or []
    ids = settings_.get("DefaultFolderIds") or []
    try:
        index = names.index("msgfolderroot")
    except ValueError:
        return None
    if index >= len(ids):
        return None
    return (ids[index] or {}).get("Id")


def parse_folders(find: dict) -> dict[str, OsepFolder]:
    """FindFolder (Traversal: Deep) → {имя папки: OsepFolder}."""
    messages = (((find.get("Body") or {}).get("ResponseMessages")) or {}).get(
        "Items"
    ) or []
    if not messages:
        return {}
    folders = ((messages[0].get("RootFolder") or {}).get("Folders")) or []
    result: dict[str, OsepFolder] = {}
    for folder in folders:
        name = folder.get("DisplayName") or ""
        if not name:
            continue
        result[name] = OsepFolder(
            name=name,
            folder_id=(folder.get("FolderId") or {}).get("Id") or "",
            total_count=int(folder.get("TotalCount") or 0),
            unread_count=int(folder.get("UnreadCount") or 0),
        )
    return result


def parse_conversation_ids(find: dict) -> list[str]:
    """FindConversation → список ConversationId (от новых к старым)."""
    conversations = ((find.get("Body") or {}).get("Conversations")) or []
    ids = []
    for conversation in conversations:
        conversation_id = (conversation.get("ConversationId") or {}).get("Id")
        if conversation_id:
            ids.append(conversation_id)
    return ids


def parse_mail_item(items_json: dict) -> MailMessage | None:
    """GetConversationItems → MailMessage (свежайшее письмо треда).

    ConversationNodes[0].Items[0]; тело = UniqueBody.Value (текст).
    """
    messages = (((items_json.get("Body") or {}).get("ResponseMessages")) or {}).get(
        "Items"
    ) or []
    if not messages:
        return None
    nodes = ((messages[0].get("Conversation") or {}).get("ConversationNodes")) or []
    if not nodes:
        return None
    items = nodes[0].get("Items") or []
    if not items:
        return None

    item = items[0]
    mailbox = (item.get("From") or {}).get("Mailbox") or {}
    attachments = []
    for attachment in item.get("Attachments") or []:
        attachments.append(
            Attachment(
                filename=attachment.get("Name") or "file",
                content_type=attachment.get("ContentType") or "",
                size=int(attachment.get("Size") or 0),
                attachment_id=(attachment.get("AttachmentId") or {}).get("Id") or "",
            )
        )
    return MailMessage(
        subject=item.get("Subject") or "",
        sender_name=mailbox.get("Name") or "",
        sender_email=mailbox.get("EmailAddress") or "",
        body=((item.get("UniqueBody") or {}).get("Value")) or "",
        has_attachments=bool(item.get("HasAttachments")),
        attachments=attachments,
    )
