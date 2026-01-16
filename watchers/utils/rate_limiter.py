import time
from collections import deque

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

    def can_request(self) -> float:
        now = time.time()


        cutoff = now - self._period
        while self._requests and self._requests[0] < cutoff:
            self._requests.popleft()

        if len(self._requests) < self._max_requests:
            return 0.0

        return self._requests[0] + self._period - now

    def record_request(self):
        wait = self.can_request()
        if wait > 0:
            raise RateLimitExceeded(f"Rate limit exceeded, wait {wait}s", wait)

        self._requests.append(time.time())