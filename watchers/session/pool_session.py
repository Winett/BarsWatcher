import aiohttp


class PoolSession:
    """Пул HTTP-сессий по (user_id, service)."""

    _sessions: dict[tuple[int, str], aiohttp.ClientSession] = {}

    @classmethod
    def get_or_create(cls, user_id: int, service: str, timeout: int = 30) -> aiohttp.ClientSession:
        key = (user_id, service)
        if key in cls._sessions and not cls._sessions[key].closed:
            return cls._sessions[key]
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
            connector=aiohttp.TCPConnector(ssl=False)
        )
        cls._sessions[key] = session
        return session

    @classmethod
    async def release(cls, user_id: int, service: str):
        key = (user_id, service)
        session = cls._sessions.pop(key, None)
        if session and not session.closed:
            await session.close()

    @classmethod
    async def release_all(cls):
        for key in list(cls._sessions.keys()):
            await cls.release(*key)
