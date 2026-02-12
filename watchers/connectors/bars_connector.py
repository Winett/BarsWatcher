import aiohttp
from typing import Dict, Optional, Any
import re
import json

from bs4 import BeautifulSoup
from loguru import logger

from watchers.services.fetcher_service import BaseFetcherService
from watchers.utils.decorators import retry
from watchers.utils.exceptions import ConnectionError, AuthError, RequestVerificationTokenError, DataParsingError, Auth2FA

from watchers.models.watcher_models import UserCredentials
from watchers.connectors.base_connector import BaseConnector
from watchers.utils.rate_limiter import RateLimiter

rate_limit = RateLimiter(max_requests=15, period_seconds=1.5)

rate_limit_fetcher = RateLimiter(max_requests=30, period_seconds=1)

class BarsConnector(BaseConnector):

    def __init__(self, credentials: UserCredentials,  base_url: str = "https://bars.mpei.ru/bars_web", timeout: int = 30):
        super().__init__(credentials, base_url, timeout)
        self.fetch = rate_limit_fetcher(self.fetch)

        self._request_verification_token = ""
        self._type_2fa_send_code = ""

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
        content = (await self.fetch('/', method="POST", data=data, allow_redirects=False)).decode()
        # async with session.post(self.base_url + '/', data=data) as response:
        #     pass

        cookies = session.cookie_jar.filter_cookies(self.base_url + '/')
        if not cookies.get("ses_bars"):
            return False
        if not cookies.get("auth_bars"):
            search = re.search(r'name="__RequestVerificationToken" type="\w+" value="(.+)" \/><input', content)
            if not search:
                raise DataParsingError("Не удалось получить страницу для получения токена __RequestVerificationToken",
                                       content)
            request_verification_token = search.group(1)
            self._request_verification_token = request_verification_token
            soup = BeautifulSoup(content, 'html.parser')
            type = soup.find("a", id="btnSend").get("onclick")
            self._type_2fa_send_code = type.split(" ")[-1].split("'")[1]
            raise Auth2FA("Требуется ввести код 2FA")

        if session.cookie_jar.filter_cookies(self.base_url + '/').get('auth_bars') or (await self.is_authenticated()):
            self._session = session
            self._save_cookies()
            return True

        return False

    async def send_2fa_code(self):

        async with self.session.get(f"https://bars.mpei.ru/bars_web/Auth/JSON_SendAF2_Code?tid={self._type_2fa_send_code}"):
            pass

    async def verify_2fa_code(self, code: str) -> bool:
        data = {
            "__RequestVerificationToken": self._request_verification_token,
            "StopOpenDefault": False,
            "Account": self.credentials.username,
            "RememberMe": True,
            "AF2_Code": code,
        }

        # content = await self.session.post('https://bars.mpei.ru/bars_web/Auth/LoginCode', data=data)
        async with self.session.post('https://bars.mpei.ru/bars_web/Auth/LoginCode', data=data) as response:
            content = await response.text()
        # if content.status == 200:
        #     self._save_cookies()
        #     return True
        # print(await content.text("utf-8"))

        if self.session.cookie_jar.filter_cookies(self.base_url + '/').get('auth_bars'):
            self._save_cookies()
            return True

        search = re.search(r'name="__RequestVerificationToken" type="\w+" value="(.+)" \/><input', content)
        if not search:
            raise DataParsingError("Не удалось получить страницу для получения токена __RequestVerificationToken",
                                   content)
        request_verification_token = search.group(1)
        self._request_verification_token = request_verification_token
        return False
