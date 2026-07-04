from loguru import logger


class AutoScaler:
    """Динамическое изменение poll_interval при ошибках/успехах.

    При ошибке: interval *= SCALE_UP_FACTOR (замедление).
    При успехе: interval *= SCALE_DOWN_FACTOR (ускорение к base).
    """

    SCALE_UP_FACTOR = 1.5
    SCALE_DOWN_FACTOR = 0.9

    def __init__(self, base_interval: int, max_interval: int = 600):
        self.base_interval = base_interval
        self.max_interval = max_interval
        self.current_interval = base_interval

    def on_error(self) -> int:
        """Увеличить интервал после ошибки. Вернуть новое значение."""
        old = self.current_interval
        self.current_interval = min(
            int(self.current_interval * self.SCALE_UP_FACTOR),
            self.max_interval
        )
        if old != self.current_interval:
            logger.debug(
                f"AutoScaler: poll_interval increased {old}s → {self.current_interval}s"
            )
        return self.current_interval

    def on_success(self) -> int:
        """Уменьшить интервал после успеха (приближение к base). Вернуть новое значение."""
        old = self.current_interval
        new_interval = int(self.current_interval * self.SCALE_DOWN_FACTOR)
        self.current_interval = max(new_interval, self.base_interval)
        if old != self.current_interval:
            logger.debug(
                f"AutoScaler: poll_interval decreased {old}s → {self.current_interval}s"
            )
        return self.current_interval

    def reset(self):
        """Сброс к base_interval."""
        self.current_interval = self.base_interval
