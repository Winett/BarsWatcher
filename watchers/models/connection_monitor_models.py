from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ConnectionStatus(Enum):
    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"

@dataclass
class ConnectionMonitorConfig:
    url: str

    poll_interval: int = 60
    max_error_count: int = 3
    timeout: int = 10

@dataclass
class ConnectionMonitorStats:
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED

    errors = 0
    last_error_time: datetime | None = None

@dataclass
class ConnectionMetrics:
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED

    error_count: int = 0
    last_error_time: datetime | None = None

    count_subscribers: int = 0

    start_time: datetime = field(default_factory=datetime.now)

    @property
    def is_connected(self) -> bool:
        return self.status == ConnectionStatus.CONNECTED

    @property
    def duration_time(self):
        return datetime.now() - self.start_time