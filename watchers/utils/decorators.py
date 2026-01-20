import asyncio
import functools
import time
from typing import Callable, Any

from loguru import logger
from watchers.utils.exceptions import AuthError, RequestVerificationTokenError


def retry(max_attempts: int = 3, delays: tuple = (1, 2, 5), exclude_exceptions: tuple = (AuthError, )):
    """Декоратор для повторных попыток"""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            last_error = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exclude_exceptions as error:
                    logger.error(f"Произошла ошибка авторизации: {error}")
                    raise
                except Exception as e:
                    last_error = e
                    if attempt < max_attempts - 1:
                        delay = delays[attempt] if attempt < len(delays) else delays[-1]
                        logger.warning(f"Попытка {attempt + 1} не удалась: {e}. Повтор через {delay}с | {func.__module__} {func.__name__}(args={args}, kwargs={kwargs})")
                        await asyncio.sleep(delay)

            raise last_error

        return wrapper

    return decorator


def log_execution_time(func: Callable) -> Callable:
    """Логирование времени выполнения"""

    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs):

        start = time.time()
        result = await func(*args, **kwargs)
        elapsed = time.time() - start
        logger.debug(f"{func.__name__} выполнен за {elapsed:.2f}с")
        return result

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs):
        start = time.time()
        result = func(*args, **kwargs)
        elapsed = time.time() - start
        logger.debug(f"{func.__name__} выполнен за {elapsed:.2f}с")
        return result

    return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper