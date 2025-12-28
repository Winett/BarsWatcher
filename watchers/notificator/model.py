from dataclasses import dataclass, field
from enum import Enum

from watchers.parser.base import Attachment

class WatcherType(Enum):
    OSEP = 'ОСЭП'
    BARS = 'БАРС'

@dataclass
class NotificatorMessage:
    message: str
    user_id: int
    watcher: WatcherType
    files: list[Attachment] = field(default_factory=list)
