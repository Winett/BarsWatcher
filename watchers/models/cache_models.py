from typing import Generic, TypeVar, Optional
from pydantic import BaseModel
from datetime import datetime

T = TypeVar('T', bound=BaseModel)


class CacheEntry(BaseModel, Generic[T]):
    key: str
    value: T
    expires_at: Optional[datetime] = None
    created_at: datetime = datetime.now()
    updated_at: datetime = datetime.now()

    @property
    def is_expired(self) -> bool:
        # Может быть ttl бесконечным
        return datetime.now() > self.expires_at if self.expires_at is not None else True