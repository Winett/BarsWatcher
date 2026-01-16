import aiohttp
import xml.etree.ElementTree as ET
from typing import Dict, Optional, Any, List
from datetime import datetime
from loguru import logger

from watchers.services.fetcher_service import BaseFetcherService
from watchers.models.mail_models import AttachmentData
from watchers.models.watcher_models import UserCredentials
from watchers.connectors.base_connector import BaseConnector


class OsepConnector(BaseConnector):

    def __init__(self, credentials: UserCredentials, base_url: str = "https://mail.mpei.ru", timeout: int = 30):
        super().__init__(credentials, base_url, timeout)

    async def _login_with_credentials(self) -> bool:
        session = self.session
        session.cookie_jar.clear()

        await self.fetch('/owa/#path=/mail', allow_redirects=False)
        # async with session.get(self.base_url + '/owa/#path=/mail', allow_redirects=False) as response:
        #     pass

        data = {
            'curl': 'Z2FowaZ2F', 'flags': 0, 'forcedownlevel': 0, 'formdir': 2, 'username': self.credentials.username,
            'password': self.credentials.password, 'isUtf8': 1, 'trusted': 4
        }
        await self.fetch("/CookieAuth.dll?Logon", data=data, allow_redirects=False)
        # async with session.post(self.base_url + "/CookieAuth.dll?Logon", data=data, allow_redirects=False) as response:
        #     pass

        if await self.is_authenticated():
            self._session = session
            self._save_cookies()
            return True
        return False

    async def is_authenticated(self) -> bool:
        async with self.session.get(self.base_url + '/owa/#path=/mail', allow_redirects=False) as response:
            if response.status == 200:
                return True
        return False

    @property
    def x_owa_canary(self):
        a = self.session.cookie_jar.filter_cookies(self.base_url).get('X-OWA-CANARY', "")
        return a.value if a else a






