from watchers.auth.base_auth import BaseAuth
from watchers.utils.rate_limiter import RateLimiter

_rate_limit = RateLimiter(max_requests=15, period_seconds=1.5)


class OsepAuth(BaseAuth):
    """Авторизация на mail.mpei.ru"""

    BASE_URL = "https://mail.mpei.ru"

    async def is_authenticated(self) -> bool:
        async with self.session.get(
            f"{self.BASE_URL}/owa/#path=/mail",
            allow_redirects=False
        ) as response:
            if response.status == 200:
                return True
        return False

    @_rate_limit
    async def _login_with_credentials(self) -> bool:
        self.session.cookie_jar.clear()

        await self.session.get(
            f"{self.BASE_URL}/owa/#path=/mail",
            allow_redirects=False
        )

        data = {
            'curl': 'Z2FowaZ2F',
            'flags': 0,
            'forcedownlevel': 0,
            'formdir': 2,
            'username': self.credentials.username,
            'password': self.credentials.password,
            'isUtf8': 1,
            'trusted': 4
        }
        await self.session.post(
            f"{self.BASE_URL}/CookieAuth.dll?Logon",
            data=data,
            allow_redirects=False
        )

        if await self.is_authenticated():
            self._save_cookies()
            return True
        return False

    async def send_2fa_code(self, *args, **kwargs):
        pass  # ОСЭП не использует 2FA

    async def verify_2fa_code(self, code: str) -> bool:
        return False

    @property
    def x_owa_canary(self):
        a = self.session.cookie_jar.filter_cookies(self.BASE_URL).get('X-OWA-CANARY', "")
        return a.value if a else a
