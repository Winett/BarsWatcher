from pydantic import BaseModel, Field

class MailFolder(BaseModel):
    folder_id: str = "_"
    display_name: str = "__"
    total_count_messages: int = 0

class MailsFolders(BaseModel):
    folders: dict[str, MailFolder] = Field(default_factory=dict)