from abc import ABC, abstractmethod
from typing import Any, Optional, Dict
import aiohttp
from pathlib import Path

class BaseFetcherService(ABC):

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self._session: Optional[aiohttp.ClientSession] = None


    @abstractmethod
    async def fetch(self, endpoint: str, method: str = "GET",
                    params: Optional[Dict] = None, data: Optional[Dict] = None) -> Any:
        pass

    @abstractmethod
    async def fetch_json(self, endpoint: str, method: str = "GET",
                         params: Optional[Dict] = None, data: Optional[Dict] = None) -> Dict:
        pass

    @property
    def session(self):
        if not self._session or self._session.closed:
            return self._create_new_session()
        return self._session

    def _create_new_session(self) -> aiohttp.ClientSession:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.timeout),
            connector=aiohttp.TCPConnector(ssl=False)
        )
        return self._session

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
