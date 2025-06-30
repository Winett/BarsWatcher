import asyncio
from hashlib import sha256
import json
from pathlib import Path
import atexit

import aiohttp
from loguru import logger

class BaseAuth:
    session_dir = None

    def __init__(self, username: str, password: str):
        self.username = username
        self.password = password
        self._session = None

        self._session_file = self.session_dir / Path(
            f"session_{self.__class__.__name__}_{self.username}_{sha256((username + password).encode()).hexdigest()}.json")
        # self.logged_in = self.login()

        atexit.register(self._save_session)

    async def login(self) -> bool:
        raise NotImplementedError

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            await self._create_session()
            await self._load_session()
        return self._session

    async def _create_session(self) -> aiohttp.ClientSession:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = aiohttp.ClientSession()
        return self._session

    async def _save_session(self):
        if self._session:
            cookies = {
                cookie.key: cookie.value
                for cookie in self._session.cookie_jar
            }
            with open(self._session_file, 'w', encoding='utf-8') as f:
                json.dump(cookies, f, ensure_ascii=False, indent=2)

    async def _load_session(self) -> bool:
        if not self._session_file.exists():
            return False

        try:
            cookies = json.loads(self._session_file.read_text())
            self._session.cookie_jar.update_cookies(cookies)
            return True
        except Exception as e:
            logger.warning(f"Ошибка загрузки сессии: {e}")
            return False

    def _cleanup(self):
        if self._session and not self._session.closed:
            asyncio.create_task(self.close())

    async def close(self):
        if self._session and not self._session.closed:
            await self._save_session()
            await self._session.close()
            self._session = None