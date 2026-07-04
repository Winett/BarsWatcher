from watchers.models.watcher_models import UserCredentials, WatcherType
from watchers.services.cache_service import AsyncFileCacher
from watchers.watchers.bars_watcher import BarsWatcher
from watchers.watchers.osep_watcher import OsepWatcher
from watchers.api.bars_api import BarsAPI
from watchers.api.osep_api import OsepAPI
from watchers.auth.bars_auth import BarsAuth
from watchers.auth.osep_auth import OsepAuth
from watchers.session.pool_session import PoolSession


class WatcherFactory:
    """Фабрика для создания вотчеров после успешной авторизации."""

    @staticmethod
    def create_bars_watcher(
        user_id: int,
        auth: BarsAuth,
        cache: AsyncFileCacher
    ) -> BarsWatcher:
        """Создание BarsWatcher после успешной авторизации в handler."""
        api = BarsAPI(auth)
        creds = UserCredentials(
            username=auth.credentials.username,
            password=auth.credentials.password,
            user_id=user_id,
            watcher_type=WatcherType.BARS
        )
        return BarsWatcher(credentials=creds, api=api, cache_service=cache)

    @staticmethod
    def create_osep_watcher(
        user_id: int,
        auth: OsepAuth,
        cache: AsyncFileCacher
    ) -> OsepWatcher:
        """Создание OsepWatcher после успешной авторизации в handler."""
        api = OsepAPI(auth)
        creds = UserCredentials(
            username=auth.credentials.username,
            password=auth.credentials.password,
            user_id=user_id,
            watcher_type=WatcherType.OSEP
        )
        return OsepWatcher(credentials=creds, api=api, cache_service=cache)

    @staticmethod
    def create_auth_and_session(
        user_id: int,
        service: str,
        login: str,
        password: str,
        watcher_type: WatcherType
    ):
        """Создание auth-объекта и сессии (перед login в handler)."""
        creds = UserCredentials(
            username=login,
            password=password,
            user_id=user_id,
            watcher_type=watcher_type
        )
        session = PoolSession.get_or_create(user_id, service)

        if watcher_type == WatcherType.BARS:
            auth = BarsAuth(creds, session)
        else:
            auth = OsepAuth(creds, session)

        return auth, session
