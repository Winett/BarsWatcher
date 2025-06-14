from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from sqlalchemy import alias


def create_env_without_data():
    try:
        with open('.env.prod') as f:
            # with open('.env', 'w') as f1:
            to_write = ""
            for string in f.readlines():
                to_write += string.split('=')[0] + "=...\n"
            with open('.env', 'w') as f1:
                f1.write(to_write)

    except FileNotFoundError:
        pass


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.prod"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str =Field(alias="TOKEN")
    DEBUG: bool = Field(default=False, alias="DEBUG")
    admins: list[int] = Field(alias='ADMINS')
    db_url: str = Field(alias='DB_URL')

settings = Settings()