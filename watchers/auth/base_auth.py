import json
from abc import ABC, abstractmethod
from pathlib import Path

import aiohttp
from loguru import logger

from settings import WORKDIR
from watchers.core.exceptions import Auth2FA, AuthError
from watchers.models.watcher_models import UserCredentials
from watchers.utils.decorators import retry


class BaseAuth(ABC):
    """Базовый класс авторизации на ресурсе.

    Отвечает за:
    1. Авторизацию через куки (если есть)
    2. Авторизацию по логину/паролю
    3. Сохранение куки после успешной авторизации
    4. Отправку 2FA кода (если применим)
    5. Отправку 2FA кода на сервер (для подтверждения)
    6. Проверку авторизации (запрос на защищённый путь)
    7. Проверку, что авторизация слетела
    """

    def __init__(self, credentials: UserCredentials, session: aiohttp.ClientSession):
        self.credentials = credentials
        self.session = session
        self._cookies_dir = WORKDIR / "sessions"
        self._cookies_dir.mkdir(exist_ok=True)
        self._cookies_file = self._cookies_dir / f"{credentials.username}_{self.__class__.__name__}.json"
        self._logger_template = f"{self.__class__.__name__} | {credentials.username} | "
        logger.debug(f"{self._logger_template} Инициализирован | cookies_file={self._cookies_file.name}")

    @abstractmethod
    async def is_authenticated(self) -> bool:
        """Проверка, что авторизация активна (запрос на защищённый путь)"""
        pass

    @abstractmethod
    async def _login_with_credentials(self) -> bool:
        """Авторизация по логину/паролю"""
        pass

    @abstractmethod
    async def send_2fa_code(self, *args, **kwargs):
        """Отправка 2FA кода пользователю (если применимо)"""
        pass

    @abstractmethod
    async def verify_2fa_code(self, code: str) -> bool:
        """Подтверждение 2FA кода на сервере"""
        pass

    async def _login_with_cookies(self) -> bool:
        """Попытка авторизации через сохранённые куки"""
        logger.debug(f"{self._logger_template} Попытка входа через куки...")
        self.session.cookie_jar.clear()
        loaded = self._load_cookies()
        if not loaded:
            logger.debug(f"{self._logger_template} Куки не найдены")
            return False
        result = await self.is_authenticated()
        logger.debug(f"{self._logger_template} Куки {'валидны' if result else 'просрочены'}")
        return result

    @retry(max_attempts=4, delays=(1, 2, 5), exclude_exceptions=(AuthError, Auth2FA,))
    async def login(self) -> bool:
        """Попытка авторизации: сначала куки, потом по данным"""
        logger.info(f"{self._logger_template} Авторизация...")
        if await self._login_with_cookies():
            logger.info(f"{self._logger_template} Авторизация через куки ✓")
            return True
        logger.debug(f"{self._logger_template} Куки не сработали, пробуем по данным...")
        self.session.cookie_jar.clear()
        if await self._login_with_credentials():
            logger.info(f"{self._logger_template} Авторизация по данным ✓")
            self._save_cookies()
            return True
        logger.warning(f"{self._logger_template} Авторизация не удалась")
        return False

    def _load_cookies(self):
        """Загрузка куки из файла"""
        if self.session and self._cookies_file.exists():
            with open(self._cookies_file, "r") as f:
                cookies = json.load(f)
            self.session.cookie_jar.update_cookies(cookies)
            logger.debug(f"{self._logger_template} Куки загружены: {len(cookies)} шт")
            return True
        return False

    def _save_cookies(self):
        """Сохранение куки в файл"""
        if self.session:
            cookies = {
                cookie.key: cookie.value
                for cookie in self.session.cookie_jar
            }
            with open(self._cookies_file, "w") as f:
                json.dump(cookies, f)
            logger.debug(f"{self._logger_template} Куки сохранены: {len(cookies)} шт")
            return True
        return False
