from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Any
from pydantic import BaseModel


class WatcherType(Enum):
    BARS = "БАРС"
    OSEP = "ОСЭП"


class WatcherStatus(Enum):
    WORKING = "WORKING"
    RESTARTING = "RESTARTING"
    ERROR = "ERROR"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"


class EventType(Enum):
    NEW_CHANGE = "NEW_CHANGE"
    PAUSE = "PAUSE"
    RESUME = "RESUME"
    EXCEPTION = "EXCEPTION"
    UNKNOWN = "UNKNOWN"


@dataclass
class WatcherEvent:
    event_type: EventType
    user_id: int
    username: str
    status: WatcherStatus
    watcher_type: WatcherType
    message: str = ""
    error: Optional[Exception] = None
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: dict = field(default_factory=dict)


@dataclass
class WatcherStats:
    start_time: datetime = field(default_factory=datetime.now)
    last_fetch_time: Optional[datetime] = None
    status: WatcherStatus = WatcherStatus.STOPPED
    last_auth_time: Optional[datetime] = None
    error_count: int = 0
    last_error_time: Optional[datetime] = None
    change_count: int = 0

    @property
    def uptime(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()

    @property
    def is_healthy(self) -> bool:
        return self.status == WatcherStatus.WORKING


@dataclass
class WatcherConfig:
    poll_interval: int = 60
    timeout: int = 30
    max_retries: int = 3
    retry_delay: int = 5
    cache_ttl: int = 86400  # 24 часа

    cache_file_ttl = 60 * 10 # 10 минут для кэша

    def validate(self) -> bool:
        return all([
            self.poll_interval > 0,
            self.timeout > 0,
            self.max_retries >= 0,
            self.retry_delay >= 0,
            self.cache_ttl > 0
        ])


@dataclass
class BarsWatcherConfig:
    """Настройки, специфичные для BarsWatcher."""
    show_marks: bool = True


@dataclass
class OsepWatcherConfig:
    """Настройки, специфичные для OsepWatcher."""
    blacklist: List[str] = field(default_factory=list)


class UserCredentials(BaseModel):
    username: str
    password: str
    user_id: int
    watcher_type: WatcherType