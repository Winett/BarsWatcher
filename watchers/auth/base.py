from abc import ABC, abstractmethod
from hashlib import sha256
from pathlib import Path

import aiohttp
from loguru import logger

from managers.cookie_manager import CookieManager

class BaseAuth(ABC):

    # session_dir = Path("sessions")
    session_dir = Path(__file__).parent.parent.parent / "sessions"
    if not session_dir.exists():
        session_dir.mkdir()

    def __init__(self, username: str, password: str, cookie_manager=None):

        self.username = username
        self.password = password

        self._session = None

        if cookie_manager is None:
            filename = f"session_{self.__class__.__name__}_{username}_{sha256((username + password).encode()).hexdigest()}.json"
            self.cookie_manager: CookieManager = CookieManager(self.session_dir / filename)
        else:
            self.cookie_manager = cookie_manager

        self._logger_template = f"{self.__class__.__name__} | {username} | "

    async def get_session(self) -> aiohttp.ClientSession:
        if not self._session or self._session.closed:
            await self._create_session()
        return self._session

    async def _create_session(self) -> None:
        connector = aiohttp.TCPConnector(ssl=False)

        self._session = aiohttp.ClientSession(
            connector=connector,
            cookie_jar=aiohttp.CookieJar(unsafe=True)
        )

    async def _save_cookies(self) -> bool:
        if self._session:
            cookies = {
                cookie.key: cookie.value
                for cookie in self._session.cookie_jar
            }
            self.cookie_manager.save_cookies(cookies)
            return True
        return False

    async def _load_cookies(self) -> bool:
        cookies = self.cookie_manager.load_cookies()
        try:
            if cookies:
                # важно: при первом вызове self._session может быть None
                session = await self.get_session()
                session.cookie_jar.update_cookies(cookies)
                return True
        except Exception as e:
            logger.error(f"{self.__class__.__name__} ({self.username}) Ошибка загрузки сессии {e =}")
        return False

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


    @abstractmethod
    async def login(self) -> bool:
        ...

    @abstractmethod
    async def is_authenticated(self) -> bool:
        ...

    # async def _request(self, method: str, url: str, **kwargs) -> aiohttp.ClientResponse | None:
    #     try:
    #         session = await self.get_session()
    #         return await session.request(method, url, **kwargs)
    #     except aiohttp.ClientError as e:
    #         logger.error(f"Ошибка соединения: {e}")
    #         return None
    #     except Exception as e:
    #         logger.error(f"Неожиданная ошибка: {e}")
    #         return None