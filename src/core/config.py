from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr, Field, field_validator
import hashlib, base64

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
