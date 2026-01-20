import time
from collections import deque
import asyncio
import time
from functools import wraps

class RateLimitExceeded(Exception):
    def __init__(self, message: str, wait_time: float):
        super().__init__(message)
        self.wait_time = wait_time

class RateLimiter:
    def __init__(self, max_requests: int, period_seconds: float):
        if max_requests <= 0:
            raise ValueError("max_requests must be greater than 0")
        if period_seconds <= 0:
            raise ValueError("period_seconds must be greater than 0")

        self._max_requests = max_requests
        self._period = period_seconds
        self._requests = deque()
        self._lock = asyncio.Lock()

    def _clean_old_requests(self, now: float) -> None:
        cutoff = now - self._period
        while self._requests and self._requests[0] < cutoff:
            self._requests.popleft()

    def _get_wait_time(self, now: float) -> float:
        self._clean_old_requests(now)

        if len(self._requests) < self._max_requests:
            return 0.0

        oldest_request_time = self._requests[0]
        return oldest_request_time + self._period - now

    async def acquire(self) -> None:
        """Дождаться возможности сделать запрос"""
        async with self._lock:
            now = time.time()
            wait_time = self._get_wait_time(now)

            if wait_time > 0:
                await asyncio.sleep(wait_time)
                now = time.time()

            self._clean_old_requests(now)

            self._requests.append(now)

    def __call__(self, func):

        @wraps(func)
        async def wrapper(*args, **kwargs):
            await self.acquire()
            return await func(*args, **kwargs)

        return wrapper

    async def __aenter__(self):
        await self.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass