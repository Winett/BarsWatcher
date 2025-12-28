from abc import ABC, abstractmethod
import aiohttp
import asyncio
from functools import wraps

from typing import Callable, Type, Awaitable, Coroutine, Any

from loguru import logger
from watchers.fetcher.exceptions import AuthError, ServerError
import json

from watchers.auth.base import BaseAuth
from dataclasses import dataclass, field


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    delays: tuple[float, ...] = (1 * 60, 2 * 60, 5 * 60)  # секунды
    exceptions: tuple[Type[Exception], ...] = field(default_factory=lambda: (
        aiohttp.ClientError,
        aiohttp.ClientConnectorError,
        aiohttp.ConnectionTimeoutError
    ))

    def __post_init__(self):
        if self.max_attempts < 1:
            raise ValueError("max_attempts должен быть >= 1")

        if len(self.delays) < 1:
            raise ValueError("delays не должен быть пустым")

        if self.max_attempts > len(self.delays) + 1:
            logger.warning(
                f"max_attempts ({self.max_attempts}) больше чем delays + 1 "
                f"({len(self.delays) + 1}), будут использованы дефолтные задержки"
            )

    def get_delay(self, attempt: int) -> float:
        if attempt < len(self.delays):
            return self.delays[attempt]
        else:
            multiplier = 2 ** (attempt - len(self.delays) + 1)
            return self.delays[-1] * multiplier

    def should_retry(self, exception: Exception, attempt: int) -> bool:
        if not any(isinstance(exception, exc_type) for exc_type in self.exceptions):
            return False

        return attempt < self.max_attempts - 1


class RetryDecorator:

    def __init__(self, policy: RetryPolicy | None = None):
        self.policy = policy or RetryPolicy()

    def __call__(self, func: Callable) -> Callable:
        if asyncio.iscoroutinefunction(func):
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                return await self._async_retry(func, *args, **kwargs)

            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                return self._sync_retry(func, *args, **kwargs)

            return sync_wrapper

    async def _async_retry(self, func: Callable, *args, **kwargs) -> Any:
        last_exception = None

        for attempt in range(self.policy.max_attempts):
            try:
                return await func(*args, **kwargs)

            except Exception as e:
                last_exception = e

                if self.policy.should_retry(e, attempt):
                    delay = self.policy.get_delay(attempt)
                    logger.warning(
                        f"Попытка {attempt + 1}/{self.policy.max_attempts} не удалась: "
                        f"{type(e).__name__}: {e}. Повтор через {delay} сек."
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    raise

        raise last_exception

    def _sync_retry(self, func: Callable, *args, **kwargs) -> Any:
        last_exception = None

        for attempt in range(self.policy.max_attempts):
            try:
                return func(*args, **kwargs)

            except Exception as e:
                last_exception = e

                if self.policy.should_retry(e, attempt):
                    delay = self.policy.get_delay(attempt)
                    logger.warning(
                        f"Попытка {attempt + 1}/{self.policy.max_attempts} не удалась: "
                        f"{type(e).__name__}: {e}. Повтор через {delay} сек."
                    )
                    import time
                    time.sleep(delay)
                    continue
                else:
                    raise

        raise last_exception


def retry(
    max_attempts: int = 3,
    delays: tuple[float, ...] = (1 * 60, 2 * 60, 5 * 60),
    exceptions: tuple[Type[Exception], ...] = (aiohttp.ClientError, aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError)
) -> Callable:
    policy = RetryPolicy(
        max_attempts=max_attempts,
        delays=delays,
        exceptions=exceptions
    )
    return RetryDecorator(policy)

def retry_with_policy(policy: RetryPolicy) -> Callable:
    return RetryDecorator(policy)


class BaseFetcher(ABC):

    def __init__(self,
                 session_provider: Callable[[], Awaitable[aiohttp.ClientSession]],
                 auth_refresh_callback: Callable[[], Awaitable[bool]],
                 # auth: BaseAuth
                 ) -> None:


        self.session_provider = session_provider
        self.auth_refresh_callback = auth_refresh_callback

        self._logger_template = f"{self.__class__.__name__} | "
        # self.auth = auth

    @staticmethod
    async def check_auth(response: aiohttp.ClientResponse) -> bool:
        return response.status != 302 and response.status != 401

    @retry()
    async def fetch_raw(self, url: str, method: str = "GET", **kwargs) -> tuple[bytes, aiohttp.ClientResponse]:
        '''

        :param method:
        :param url:
        :param kwargs:
        :return:
        exception: AuthError, ServerError
        '''
        session = await self.session_provider()

        async with session.request(method, url, allow_redirects=False, **kwargs) as response:
            # Проверяем авторизацию
            if not await self.check_auth(response):
                logger.warning(self._logger_template + f"Требуется обновление авторизации для {url}")
                refreshed = await self.auth_refresh_callback()
                if refreshed:
                    session = await self.session_provider()
                    session.headers.update({"X-OWA-CANARY": session.cookie_jar.filter_cookies(url).get("X-OWA-CANARY").value})
                    async with session.request(method, url, **kwargs) as retry_response:
                        if await self.check_auth(retry_response):
                            return await retry_response.read(), retry_response
                        else:
                            raise AuthError(f"Не удалось обновить авторизацию для {url}")
                else:
                    raise AuthError(f"Не удалось обновить авторизацию для {url}")


            if response.status >= 500:
                raise ServerError(f"Ошибка сервера {response.status} для {url}")
            elif response.status >= 400 and response.status != 404:
                raise aiohttp.ClientResponseError(
                    response.request_info,
                    response.history,
                    status=response.status,
                    message=f"HTTP ошибка {response.status}"
                )

            return await response.read(), response

    @abstractmethod
    async def fetch(self, url: str, **kwargs) -> tuple[Any, Any]:
        ...
