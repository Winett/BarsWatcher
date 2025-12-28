from .base import BaseAuth
from .utils import extract_request_verification_token

from loguru import logger

class BarsAuth(BaseAuth):
    # Все ошибки сетевые обрабатывать в высоком уровне
    login_url = 'https://bars.mpei.ru/bars_web/'
    summary_url = 'https://bars.mpei.ru/bars_web/ST_Study/Main/_PartialSummary'
    list_student_url = 'https://bars.mpei.ru/bars_web/ST/Student/ListStudent'

    async def _login_with_cookies(self):
        session = await self.get_session()
        if await self._load_cookies():
            async with session.get(self.list_student_url, allow_redirects=False) as response:
                if response.status == 200:
                    return True
        return False

    async def _login_with_credentials(self):
        session = await self.get_session()

        session.cookie_jar.clear()

        async with session.get(self.login_url, allow_redirects=False) as response:
            content = await response.read()


        RequestVerificationToken = extract_request_verification_token(content)
        if not RequestVerificationToken:
            logger.error(self._logger_template + "Нет токена RequestVerificationToken на главной странице")
            return False

        data = {
            "__RequestVerificationToken": RequestVerificationToken,
            "StopOpenDefault": False,
            "Account": self.username,
            "Password": self.password,
            "RememberMe": True
        }

        async with session.post(self.login_url, data=data) as response:
            pass

        async def check_auth():
            async with session.get(self.list_student_url, allow_redirects=False) as response:
                if response.status == 200:
                    return True
            return False

        if session.cookie_jar.filter_cookies(self.login_url).get('auth_bars') or (await check_auth()):
            self._session = session
            await self._save_cookies()
            return True

        return False

    async def login(self) -> bool:
        if await self._login_with_cookies():
            return True
        if await self._login_with_credentials():
            return True
        return False

    async def is_authenticated(self) -> bool:
        session = await self.get_session()
        async with session.get(self.list_student_url, allow_redirects=False) as response:
            if response.status == 200:
                return True
        return False

