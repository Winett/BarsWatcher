import re

from bs4 import BeautifulSoup
from loguru import logger

from watchers.auth.base_auth import BaseAuth
from watchers.core.exceptions import (
    Auth2FA, DataParsingError, RequestVerificationTokenError
)
from watchers.utils.decorators import retry
from watchers.utils.rate_limiter import RateLimiter

_rate_limit = RateLimiter(max_requests=15, period_seconds=1.5)


class BarsAuth(BaseAuth):
    """Авторизация на bars.mpei.ru"""

    BASE_URL = "https://bars.mpei.ru/bars_web"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._request_verification_token = ""
        self._type_2fa_send_code = ""

    async def is_authenticated(self) -> bool:
        async with self.session.get(
            f"{self.BASE_URL}/ST/Student/ListStudent",
            allow_redirects=False
        ) as response:
            if response.status == 200:
                return True
        return False

    @_rate_limit
    async def _login_with_credentials(self) -> bool:
        self.session.cookie_jar.clear()

        content = (await self.session.get(
            f"{self.BASE_URL}/",
            allow_redirects=False
        )).read().decode()

        search = re.search(
            r'name="__RequestVerificationToken" type="\w+" value="(.+)" \/><input',
            content
        )
        if not search:
            raise DataParsingError(
                "Не удалось получить страницу для получения токена __RequestVerificationToken",
                content
            )
        request_verification_token = search.group(1)
        if not request_verification_token:
            raise RequestVerificationTokenError(
                "Не удалось извлечь токен __RequestVerificationToken",
                content=content
            )

        data = {
            "__RequestVerificationToken": request_verification_token,
            "StopOpenDefault": False,
            "Account": self.credentials.username,
            "Password": self.credentials.password,
            "RememberMe": True
        }
        content = (await self.session.post(
            f"{self.BASE_URL}/",
            data=data,
            allow_redirects=False
        )).read().decode()

        cookies = self.session.cookie_jar.filter_cookies(f"{self.BASE_URL}/")
        if not cookies.get("ses_bars"):
            return False
        if not cookies.get("auth_bars"):
            search = re.search(
                r'name="__RequestVerificationToken" type="\w+" value="(.+)" \/><input',
                content
            )
            if not search:
                raise DataParsingError(
                    "Не удалось получить страницу для получения токена __RequestVerificationToken",
                    content
                )
            request_verification_token = search.group(1)
            self._request_verification_token = request_verification_token
            soup = BeautifulSoup(content, 'html.parser')
            onclick = soup.find("a", id="btnSend").get("onclick")
            self._type_2fa_send_code = onclick.split(" ")[-1].split("'")[1]
            raise Auth2FA("Требуется ввести код 2FA")

        if (self.session.cookie_jar.filter_cookies(f"{self.BASE_URL}/").get('auth_bars')
                or await self.is_authenticated()):
            self._save_cookies()
            return True

        return False

    async def send_2fa_code(self):
        await self.session.get(
            f"{self.BASE_URL}/Auth/JSON_SendAF2_Code?tid={self._type_2fa_send_code}"
        )

    async def verify_2fa_code(self, code: str) -> bool:
        data = {
            "__RequestVerificationToken": self._request_verification_token,
            "StopOpenDefault": False,
            "Account": self.credentials.username,
            "RememberMe": True,
            "AF2_Code": code,
        }
        async with self.session.post(
            f"{self.BASE_URL}/Auth/LoginCode",
            data=data
        ) as response:
            content = await response.text()

        if self.session.cookie_jar.filter_cookies(f"{self.BASE_URL}/").get('auth_bars'):
            self._save_cookies()
            return True

        search = re.search(
            r'name="__RequestVerificationToken" type="\w+" value="(.+)" \/><input',
            content
        )
        if not search:
            raise DataParsingError(
                "Не удалось получить страницу для получения токена __RequestVerificationToken",
                content
            )
        request_verification_token = search.group(1)
        self._request_verification_token = request_verification_token
        return False
