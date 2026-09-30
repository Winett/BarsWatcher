import base64
import hashlib

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ["settings"]


class Settings(BaseSettings):
    secret_key: SecretStr

    telegram_bot_token: SecretStr
    debug: bool = False
    admins: list[int]

    db_user: str
    db_password: str
    db_host: str
    db_port: int
    db_name: str

    redis_url: str

    # режим интеграций: sim (симуляция) | file (ответы из файлов) | real
    integration_mode: str = "sim"

    # частые проверки в тестовых режимах (sim/file): правки файлов ответов
    # быстро превращаются в события; для real не используется
    sim_poll_interval_seconds: int = 10

    # напоминания о проставлении пар в тестовых режимах: каждые N секунд
    # (0 — выключено; для real не используется, там 18:00 GMT+3)
    sim_reminder_interval_seconds: int = 0

    # планировщик (тик проверки «пора ли»; компенсирует шаг интервалов)
    scheduler_tick_seconds: int = 5

    # TTL снимков для diff (сек)
    snapshot_ttl_seconds: int = 24 * 3600

    # начальные глобальные настройки: импортируются в БД при первом запуске
    # (global_settings пуста); дальше меняются из админки и живут в БД
    poll_interval_seconds: int = 180
    max_poll_interval_seconds: int = 1800
    request_timeout_seconds: int = 30
    error_timeout_seconds: int = 300
    auto_scale_enabled: bool = True
    notify_service_status: bool = False
    notify_new_users: bool = True

    @property
    def database_url(self):
        return f"postgresql+asyncpg://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

    @field_validator("secret_key", mode="before")
    @classmethod
    def assemble_fernet_key(cls, raw_password: str) -> str:
        salt = b"f1eca7bef88afb1fc427aa50b1877be7"

        key = hashlib.pbkdf2_hmac(
            hash_name="sha256",
            password=raw_password.encode("utf-8"),
            salt=salt,
            iterations=100_000,
            dklen=32,
        )

        return base64.urlsafe_b64encode(key).decode("utf-8")

    model_config = SettingsConfigDict(env_file=(".env.example", ".env"), extra="ignore")


settings = Settings()
