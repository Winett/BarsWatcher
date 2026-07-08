from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ConnectionStatus(Enum):
    UNKNOWN = "UNKNOWN"             # Начальное состояние
    CONNECTING = "CONNECTING"       # Идёт проверка
    CONNECTED = "CONNECTED"         # Стабильно доступен
    DEGRADED = "DEGRADED"           # Доступен, но медленно
    DISCONNECTED = "DISCONNECTED"   # Недоступен
    RECOVERING = "RECOVERING"       # Восстанавливается после DISCONNECTED


@dataclass
class ConnectionMonitorConfig:
    url: str

    # Проверка соединения
    poll_interval: int = 60          # Интервал проверки (сек)
    timeout: int = 10                # Таймаут одного запроса (сек)

    # Пороги для DISCONNECTED
    failure_threshold: int = 5       # Подряд ошибок для "упал"

    # Пороги для восстановления
    recovery_threshold: int = 3      # Подряд OK для "восстановился"

    # Пороги для DEGRADED
    slow_threshold: float = 3.0      # Секунд для "медленно"
    degraded_checks: int = 3         # Сколько проверок подряд "медленно" для DEGRADED

    # Staggered resume
    stagger_delay: float = 2.0       # Базовая задержка между вотчерами (сек)
    stagger_jitter: float = 3.0      # Случайный разброс (сек)


@dataclass
class ConnectionMetrics:
    status: ConnectionStatus = ConnectionStatus.UNKNOWN

    error_count: int = 0             # Текущий счётчик ошибок подряд
    success_count: int = 0           # Текущий счётчик успехов подряд
    slow_count: int = 0              # Текущий счётчик медленных проверок подряд

    last_error_time: datetime | None = None
    last_success_time: datetime | None = None
    last_response_time: float = 0.0  # Последнее время ответа (сек)

    total_checks: int = 0            # Всего проверок
    total_errors: int = 0            # Всего ошибок

    start_time: datetime = field(default_factory=datetime.now)

    @property
    def is_connected(self) -> bool:
        return self.status in (ConnectionStatus.CONNECTED, ConnectionStatus.DEGRADED)

    @property
    def uptime_seconds(self) -> float:
        return (datetime.now() - self.start_time).total_seconds()

    @property
    def error_rate(self) -> float:
        if self.total_checks == 0:
            return 0.0
        return self.total_errors / self.total_checks
