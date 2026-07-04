import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any, Optional, Dict

import aiohttp
from loguru import logger

from watchers.auth.base_auth import BaseAuth
from watchers.core.exceptions import AuthError, ResponseError, ConnectionError
from watchers.utils.decorators import retry


class BaseAPI(ABC):
    """Базовый API-клиент с автоматической авторизацией.

    Запросы с авторизацией: если сессия слетела — re-login и повтор запроса.
    """

    def __init__(self, auth: BaseAuth, base_url: str):
        self.auth = auth
        self.session = auth.session
        self.base_url = base_url
        self._logger_template = f"{self.__class__.__name__} | {auth.credentials.username} | "
        logger.debug(f"{self._logger_template} Инициализирован | base_url={base_url}")

    @retry(max_attempts=4, delays=(1, 2, 5), exclude_exceptions=(AuthError,))
    async def _request_with_authorization(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        **kwargs
    ) -> bytes:
        """Запрос с проверкой авторизации. Если сессия слетела — re-login."""
        logger.debug(f"{self._logger_template} {method} {endpoint}")
        try:
            async with self.session.request(
                method, self.base_url + endpoint,
                params=params, data=data,
                allow_redirects=False, **kwargs
            ) as response:
                if response.status in [302, 401]:
                    logger.warning(f"{self._logger_template} {method} {endpoint} → {response.status} (auth required)")
                    raise AuthError("Ошибка авторизации")
                logger.debug(f"{self._logger_template} {method} {endpoint} → {response.status}")
                return await response.content.read()
        except AuthError:
            logger.info(f"{self._logger_template} Re-login из-за {endpoint}...")
            res = await self.auth.login()
            if not res:
                raise AuthError("Не удалось повторно авторизоваться")
            if kwargs.get('headers') and hasattr(self.auth, 'x_owa_canary'):
                kwargs['headers'] = {
                    **kwargs['headers'],
                    'X-OWA-CANARY': self.auth.x_owa_canary
                }
            async with self.session.request(
                method, self.base_url + endpoint,
                params=params, data=data,
                allow_redirects=False, **kwargs
            ) as response:
                if response.status in [302, 401]:
                    logger.warning(f"{self._logger_template} {method} {endpoint} → {response.status} (auth failed after re-login)")
                    raise AuthError("Ошибка авторизации")
                logger.debug(f"{self._logger_template} {method} {endpoint} → {response.status} (после re-login)")
                return await response.content.read()

    @retry(max_attempts=4, delays=(1, 2, 5))
    async def _request(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        **kwargs
    ) -> bytes:
        """Запрос без проверки авторизации"""
        logger.debug(f"{self._logger_template} {method} {endpoint} (без auth)")
        try:
            async with self.session.request(
                method, self.base_url + endpoint,
                params=params, data=data, **kwargs
            ) as response:
                logger.debug(f"{self._logger_template} {method} {endpoint} → {response.status}")
                return await response.content.read()
        except (aiohttp.ClientConnectorError, asyncio.TimeoutError) as e:
            logger.error(f"{self._logger_template} {method} {endpoint} → {type(e).__name__}: {e}")
            raise ConnectionError("Ошибка подключения")

    async def _fetch_json(
        self,
        endpoint: str,
        method: str = "GET",
        params: Optional[Dict] = None,
        data: Optional[Dict] = None,
        **kwargs
    ) -> Dict:
        """Запрос с парсингом JSON"""
        response = await self._request_with_authorization(endpoint, method, params=params, data=data, **kwargs)
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            logger.error(f"{self._logger_template} JSON parse error on {endpoint}")
            raise ResponseError(message="Ошибка декодирования из json", content=str(response))
