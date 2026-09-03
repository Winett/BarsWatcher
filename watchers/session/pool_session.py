import aiohttp


class PoolSession:
    """Пул HTTP-сессий по (user_id, service).

    - TCPConnector разделяется между пользователями на один домен (DNS-кэш, keepalive)
    - connector_owner=False: закрытие сессии НЕ закрывает коннектор
    - cookie_jar переносится из закрытой сессии (защита от реавторизации)
    """

    _connectors: dict[str, aiohttp.TCPConnector] = {}
    _sessions: dict[tuple[int, str], aiohttp.ClientSession] = {}

    @classmethod
    def _get_connector(cls, domain: str) -> aiohttp.TCPConnector:
        if domain not in cls._connectors:
            cls._connectors[domain] = aiohttp.TCPConnector(
                ssl=False,
                limit=100,
                limit_per_host=10,
                enable_cleanup_closed=True,
                keepalive_timeout=30,
                ttl_dns_cache=300,
            )
        return cls._connectors[domain]

    @classmethod
    def get_or_create(cls, user_id: int, service: str, timeout: int = 30) -> aiohttp.ClientSession:
        key = (user_id, service)
        if key in cls._sessions and not cls._sessions[key].closed:
            return cls._sessions[key]

        # Определяем домен из service или используем дефолт
        domain = cls._service_to_domain(service)

        # Передаём cookie_jar из закрытой сессии (сохраняем авторизацию)
        old_session = cls._sessions.get(key)
        cookie_jar = old_session.cookie_jar if old_session and old_session.closed else aiohttp.CookieJar()

        session = aiohttp.ClientSession(
            connector=cls._get_connector(domain),
            connector_owner=False,  # Коннектор не закрывается при закрытии сессии
            timeout=aiohttp.ClientTimeout(total=timeout),
            cookie_jar=cookie_jar,
        )

        cls._sessions[key] = session
        return session

    @classmethod
    async def release(cls, user_id: int, service: str):
        key = (user_id, service)
        session = cls._sessions.pop(key, None)
        if session and not session.closed:
            await session.close()
            # Коннектор НЕ закрывается — он шарится между пользователями

    @classmethod
    async def release_all(cls):
        """Закрыть все сессии и коннекторы (вызывается при shutdown)."""
        for key in list(cls._sessions.keys()):
            await cls.release(*key)

        # Закрываем все коннекторы только при полном shutdown
        for connector in cls._connectors.values():
            if not connector.closed:
                await connector.close()
        cls._connectors.clear()

    @classmethod
    def _service_to_domain(cls, service: str) -> str:
        return {
            "bars": "bars.mpei.ru",
            "osep": "mail.mpei.ru",
        }.get(service, service)
