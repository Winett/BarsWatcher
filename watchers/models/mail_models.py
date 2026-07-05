from pydantic import BaseModel, Field
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime


class FolderId(BaseModel):
    change_key: str = Field(alias="ChangeKey", default="")
    id: str = Field(alias="Id", default="")

class Folder(BaseModel):
    child_folder_count: int = Field(alias="ChildFolderCount", default=0)
    display_name: str = Field(alias="DisplayName", default="")
    effective_rights: dict = Field(alias="EffectiveRights", default_factory=dict)
    extended_property: list = Field(alias="ExtendedProperty", default_factory=list)
    folder_class: str = Field(alias="FolderClass", default="")
    folder_id: FolderId = Field(alias="FolderId", default_factory=FolderId)
    parent_folder_id: FolderId = Field(alias="ParentFolderId", default_factory=FolderId)
    total_count: int = Field(alias="TotalCount", default=0)
    unread_count: int = Field(alias="UnreadCount", default=0)

    class Config:
        populate_by_name = True

class Folders(BaseModel):
    folders: list[Folder] = Field(default=list, alias='Folders')

class ConversationId(BaseModel):
    id: str = Field(alias="Id", default="")

class MailBox(BaseModel):
    email_address: str = Field(alias="EmailAddress", default="")
    mailbox_type: str = Field(alias="MailboxType", default="")
    name: str = Field(alias="Name", default="")
    routing_type: str = Field(alias="RoutingType", default="")

class FromMessageReceived(BaseModel):
    mail_box: MailBox = Field(alias="Mailbox", default_factory=MailBox)

class ItemId(FolderId):
    ...
class ReceivedRepresenting(FromMessageReceived):
    ...

class Sender(FromMessageReceived):
    ...

class RecipientCounts(BaseModel):
    to_recipients_count: int = Field(alias="ToRecipientsCount", default=0)

class Recipient(BaseModel):
    email_address: str = Field(alias="EmailAddress", default="")
    mailbox_type: str = Field(alias="MailboxType", default="")
    name: str = Field(alias="Name", default="")
    routing_type: str = Field(alias="RoutingType", default="")
    sip_uri: str = Field(alias="SipUri", default="")
    submitted: bool = Field(alias="Submitted", default=False)

class UniqueBody(BaseModel):
    body_type: str = Field(alias="BodyType", default="")
    is_truncated: bool = Field(alias="IsTruncated", default=False)
    value: str = Field(alias="Value", default="")

class AttachmentId(ConversationId):
    ...

class Attachment(BaseModel):
    attachment_id: AttachmentId = Field(alias="AttachmentId", default_factory=AttachmentId)
    content_id: str = Field(alias="ContentId", default="")
    content_type: str = Field(alias="ContentType", default="")
    last_modified_time: datetime = Field(alias="LastModifiedTime", default_factory=datetime.now)
    name: str = Field(alias="Name", default="")
    size: int = Field(alias="Size", default=0)

class Conversation(BaseModel):
    attachments: list[Attachment] = Field(default_factory=list, alias='Attachments')
    block_status: bool = Field(alias="BlockStatus", default=False)
    conversation_id: ConversationId = Field(alias="ConversationId", default_factory=ConversationId)
    date_time_created: datetime = Field(alias="DateTimeCreated", default_factory=datetime.now)
    date_time_received: datetime = Field(alias="DateTimeReceived", default_factory=datetime.now)
    date_time_sent: datetime = Field(alias="DateTimeSent", default_factory=datetime.now)
    display_to: str | list[str] = Field(alias="DisplayTo", default_factory=list)
    from_: FromMessageReceived = Field(alias="From", default_factory=FromMessageReceived)
    has_attachments: bool = Field(alias="HasAttachments", default=False)
    has_blocked_images: bool = Field(alias="HasBlockedImages", default=False)
    importance: str = Field(alias="Importance", default="")
    is_read: bool = Field(alias="IsRead", default=False)
    instance_key: str = Field(alias="InstanceKey", default="")
    internet_message_id: str = Field(alias="InternetMessageId", default="")
    is_draft: bool = Field(alias="IsDraft", default=False)
    is_group_escalation_message: bool = Field(alias="IsGroupEscalationMessage", default=False)
    is_read_receipt_requested: bool = Field(alias="IsReadReceiptRequested", default=False)
    item_id: ItemId = Field(alias="ItemId", default_factory=ItemId)
    last_modified_time: datetime = Field(alias="LastModifiedTime", default_factory=datetime.now)
    parent_folder_id: FolderId = Field(alias="ParentFolderId", default=FolderId())
    received_representing: ReceivedRepresenting = Field(alias="ReceivedRepresenting", default_factory=ReceivedRepresenting)
    recipient_counts: RecipientCounts= Field(alias="RecipientCounts", default_factory=RecipientCounts)
    response_objects: list = Field(alias="ResponseObjects", default_factory=list)
    sender: Sender = Field(alias="Sender", default_factory=Sender)
    sensitivity: str = Field(alias="Sensitivity", default="")
    size: int = Field(alias="Size", default=0)
    subject: str = Field(alias="Subject", default="")
    to_recipients: list[Recipient] = Field(alias="ToRecipients", default_factory=list)
    unique_body: UniqueBody = Field(alias="UniqueBody", default_factory=UniqueBody)

class Items(BaseModel):
    items: list[Conversation] = Field(alias="Items", default_factory=list)
    is_root_node: bool = Field(alias="IsRootNode", default=False)
    internet_message_id: str = Field(alias="InternetMessageId", default="")

class ConversationNodes(BaseModel):
    conversation_nodes: list[Items] = Field(alias="ConversationNodes", default_factory=list)


class NewMailEvent(BaseModel):
    # Обязательно проверять на пустоту conversation_id
    event_type: str = Field(alias="EventType", default="0")
    id: str = Field(alias="id", default="")
    conversation_id: str = Field(alias="ConversationId", default="")
    is_clutter: bool = Field(alias="IsClutter", default=False)
    item_id: str = Field(alias="ItemId", default="")


class AttachmentData(BaseModel):
    id: str
    content_type: str
    filename: str
    size: int
    content: Optional[bytes] = None


class MailMessage(BaseModel):
    conversation_id: str
    subject: str
    sender_email: str
    sender_name: str
    body: str
    has_attachments: bool = False
    attachments: List[AttachmentData] = Field(default=list)
    received_at: datetime = Field(default_factory=datetime.now)

    def format_message(self) -> str:
        return (
                f"От: {self.sender_name}\n"
                f"Тема: {self.subject}\n\n"
                f"{self.body}"
                + ("\n\nЕсть вложения!" if self.has_attachments else "")
        )