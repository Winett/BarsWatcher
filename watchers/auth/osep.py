from .base import BaseAuth

class OsepAuth(BaseAuth):
    # logon_url = "https://mail.mpei.ru/CookieAuth.dll?GetLogon?curl=Z2FowaZ2F&reason=0&formdir=2"
    login_url = "https://mail.mpei.ru/CookieAuth.dll?Logon"
    main_url = "https://mail.mpei.ru/owa/#path=/mail"
    mails_url = 'https://mail.mpei.ru/owa/sessiondata.ashx?appcacheclient=0'

    def __init__(self, username, password):
        super().__init__(username, password)

    async def check_auth(self, session) -> bool:
        async with session.post(self.mails_url, allow_redirects=False, ssl=False) as response:
            return response.status == 200

    async def _login_with_cookies(self):
        session = await self.get_session()
        if await self._load_cookies():
            async with session.get(self.main_url, allow_redirects=False) as response:
                if response.status == 200:
                    return True
        return False

    async def _login_with_credentials(self):
        session = await self.get_session()

        session.cookie_jar.clear()

        async with session.get(self.mails_url, allow_redirects=False) as response:
            pass

        data = {
            'curl': 'Z2FowaZ2F', 'flags': 0, 'forcedownlevel': 0, 'formdir': 2, 'username': self.username,
            'password': self.password, 'isUtf8': 1, 'trusted': 4
        }

        async with session.post(self.login_url, data=data, allow_redirects=False) as response:
            pass

        if await self.check_auth(session):
            self._session = session
            await self._save_cookies()
            return True
        return False

    async def is_authenticated(self) -> bool:
        session = await self.get_session()
        return await self.check_auth(session)

    async def login(self) -> bool:
        if await self._login_with_cookies():
            return True
        if await self._login_with_credentials():
            return True
        return False

