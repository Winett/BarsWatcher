from abc import ABC, abstractmethod

import aiohttp
import asyncio
from typing import Dict, Optional, Any, Awaitable, Callable
import json
from pathlib import Path
from settings import WORKDIR

from watchers.services.fetcher_service import BaseFetcherService
from watchers.utils.decorators import retry
from watchers.utils.exceptions import ConnectionError, AuthError, ResponseError
from watchers.models.watcher_models import UserCredentials

retrier = retry(max_attempts=3, delays=(1, 2, 5), exclude_exceptions=(AuthError, ))

class BaseConnector(BaseFetcherService, ABC):
    session_dir = WORKDIR / Path("sessions/")

    def __init__(self, credentials: UserCredentials, base_url: str = "https://bars.mpei.ru", timeout: int = 30):
        super().__init__(timeout=timeout)

        self.base_url = base_url
        self.credentials = credentials
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._session_file = self.session_dir / f"{credentials.username}_{self.__class__.__name__}.json"

    # @abstractmethod
    # async def authenticate(self) -> bool:
    #     '''Метод для авториазации на ресурсе'''
    #     pass

    @abstractmethod
    async def is_authenticated(self) -> bool:
        pass

    def _load_cookies(self):
        '''Загрузка куки из файла, если есть'''
        if self.session and self._session_file.exists():
            with open(self._session_file, "r") as f:
                cookies = json.load(f)
            self.session.cookie_jar.update_cookies(cookies)
            return True

        return False

    def _save_cookies(self):
        """Сохранение куки в файл"""
        if self.session:
            cookies = {
                cookie.key: cookie.value
                for cookie in self.session.cookie_jar
            }
            with open(self._session_file, "w") as f:
                json.dump(cookies, f)
            return True
        return False


    async def _login_with_cookies(self) -> bool:
        """Попытка авторизации с помощью куки"""
        self.session.cookie_jar.clear()
        self._load_cookies()
        return await self.is_authenticated()

    @abstractmethod
    async def _login_with_credentials(self) -> bool:
        """Попытка авторизации с помощью учетных данных"""
        pass

    @retrier
    async def login(self) -> bool:
        """Попытка авторизации"""
        if await self._login_with_cookies():
            return True
        self.session.cookie_jar.clear()
        if await self._login_with_credentials():
            return True
        return False

    async def _check_authorization_and_fetch(self, fetch: Callable[[Any], Awaitable[Any]], *args, **kwargs) -> Any:
        """
        Проверяет авторизацию и выполняет запрос, иначе пытается авторизацию и повторить запрос

        :except AuthError: Неверный логин или пароль
        """
        if self.is_authenticated():
            return await fetch(*args, **kwargs)
        else:
            try:
                if not await self.login():
                    raise AuthError("Ошибка авторизации")
            except AuthError:
                raise
            except (aiohttp.ClientConnectorError, asyncio.TimeoutError):
                raise ConnectionError("Ошибка подключения")

            return await fetch(*args, **kwargs)

    @retrier
    async def _request_with_authorization(self, endpoint: str, method: str = "GET", params: Optional[Dict] = None, data: Optional[Dict] = None, **kwargs):
        try:
            async with self.session.request(method, self.base_url + endpoint, params=params, data=data, allow_redirects=False, **kwargs) as response:
                if response.status >= 300:
                    raise AuthError("Ошибка авторизации")
                return await response.content.read()
        except AuthError:
            res = await self.login()
            if kwargs.get('headers'):
                kwargs['headers'] = {**kwargs['headers'], 'X-OWA-CANARY': self.x_owa_canary}
            async with self.session.request(method, self.base_url + endpoint, params=params, data=data, allow_redirects=False,
                                            **kwargs) as response:
                if response.status >= 300:
                    raise AuthError("Ошибка авторизации")
                return await response.content.read()

    @retrier
    async def fetch(self, endpoint: str, method: str = "GET",
                    params: Optional[Dict] = None, data: Optional[Dict] = None, **kwargs) -> Any:
        try:
            async with self.session.request(method, self.base_url + endpoint, params=params, data=data, **kwargs) as response:
                return await response.content.read()
        except (aiohttp.ClientConnectorError, asyncio.TimeoutError) as e:
            raise ConnectionError("Ошибка подключения")


    async def fetch_json(self, endpoint: str, method: str = "GET",
                         params: Optional[Dict] = None, data: Optional[Dict] = None, **kwargs) -> Dict:
        """

        :param endpoint:
        :param method: GET, POST, PUT, DELETE, HEAD, OPTIONS
        :param params:
        :param data:
        :param kwargs:
        :return:
        :exception: ResponseError: Ошибка, если не удалось распарсить json объект
        """
        response = await self.fetch(endpoint, method, params=params, data=data, **kwargs)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            raise ResponseError(message="Ошибка декодирования из json", content=response)


    async def close(self):
        self._save_cookies()
        await super().close()

