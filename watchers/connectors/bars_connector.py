import aiohttp
from typing import Dict, Optional, Any
import re
import json
from loguru import logger

from watchers.services.fetcher_service import BaseFetcherService
from watchers.utils.decorators import retry
from watchers.utils.exceptions import ConnectionError, AuthError, RequestVerificationTokenError, DataParsingError

from watchers.models.watcher_models import UserCredentials
from watchers.connectors.base_connector import BaseConnector
from watchers.utils.rate_limiter import RateLimiter

rate_limit = RateLimiter(max_requests=15, period_seconds=1.5)

rate_limit_fetcher = RateLimiter(max_requests=30, period_seconds=1)

class BarsConnector(BaseConnector):

    def __init__(self, credentials: UserCredentials,  base_url: str = "https://bars.mpei.ru/bars_web", timeout: int = 30):
        super().__init__(credentials, base_url, timeout)
        self.fetch = rate_limit_fetcher(self.fetch)

    async def is_authenticated(self) -> bool:
        async with self.session.get(self.base_url + '/ST/Student/ListStudent', allow_redirects=False) as response:
            if response.status == 200:
                return True
        return False

    @rate_limit
    async def _login_with_credentials(self) -> bool:
        session = self.session
        session.cookie_jar.clear()
        content = (await self.fetch('/', allow_redirects=False)).decode()
        # async with session.get(self.base_url + '/', allow_redirects=False) as response:
        #     content = await response.read()
        search = re.search(r'name="__RequestVerificationToken" type="\w+" value="(.+)" \/><input', content)
        if not search:
            raise DataParsingError("Не удалось получить страницу для получения токена __RequestVerificationToken", content)
        request_verification_token = search.group(1)
        if not request_verification_token:
            raise RequestVerificationTokenError("Не удалось извлеть токен __RequestVerificationToken", content=content)

        data = {
            "__RequestVerificationToken": request_verification_token,
            "StopOpenDefault": False,
            "Account": self.credentials.username,
            "Password": self.credentials.password,
            "RememberMe": True
        }
        await self.fetch('/', method="POST", data=data)
        # async with session.post(self.base_url + '/', data=data) as response:
        #     pass


        if session.cookie_jar.filter_cookies(self.base_url + '/').get('auth_bars') or (await self.is_authenticated()):
            self._session = session
            self._save_cookies()
            return True

        return False

