from pydantic import BaseModel, Field, field_validator

class AttachmentData(BaseModel):
    name: str = Field(alias='Name')
    content_type: str = Field(alias='ContentType')
    id: str = Field(alias='AttachmentId')

    @field_validator('id', mode='before')
    def parse_id(cls, v):
        return v['Id']

class Attachments(BaseModel):
    attachments: list[AttachmentData]