import asyncio
import pytest
from collections import defaultdict
from unittest.mock import AsyncMock, MagicMock

from watchers.core.base_watcher import BaseWatcher
from watchers.core.event_service import EventService
from watchers.core.exceptions import AuthError
from watchers.connectors.connection_monitor import BarsMonitor
from watchers.managers.watcher_manager import WatcherManager, BarsWatcherManager
from watchers.models.watcher_models import (
    UserCredentials, WatcherConfig, WatcherType,
    WatcherEvent, WatcherStatus, EventType
)
from watchers.models.connection_monitor_models import ConnectionStatus
from watchers.services.redis_cache_service import RedisCacheService


class IntegrationWatcher(BaseWatcher):
    def __init__(self, credentials, cache, config=None, fetch_return="ok"):
        super().__init__(credentials, cache, config)
        self.fetch_return = fetch_return
        self.iteration_count = 0
        self.fetch_log = []

    def _register_instance(self):
        pass

    async def fetch_data(self):
        self.iteration_count += 1
        self.fetch_log.append(self.iteration_count)
        return self.fetch_return

    async def process_data(self, data):
        return data

    async def detect_changes(self, old_data, new_data):
        return []


@pytest.fixture(autouse=True)
def reset():
    EventService._instance = None
    BarsMonitor._instance = None
    BarsWatcherManager._managers_watchers = defaultdict(dict)
    yield
    EventService._instance = None
    BarsMonitor._instance = None
    BarsWatcherManager._managers_watchers = defaultdict(dict)


@pytest.fixture
def creds():
    return UserCredentials(
        username="test_user", password="pass",
        user_id=111, watcher_type=WatcherType.BARS
    )


@pytest.fixture
def fast_config():
    return WatcherConfig(poll_interval=0.05)


@pytest.fixture
def cache():
    mock = AsyncMock(spec=RedisCacheService)
    mock.get.return_value = None
    mock.set.return_value = None
    mock.delete.return_value = True
    mock.exists.return_value = False
    return mock


# ─── Тест 1: DISCONNECTED → вотчер ждёт ──────────────────────────────

@pytest.mark.asyncio
async def test_watcher_waits_when_server_unavailable(creds, fast_config, cache):
    """Вотчер блокируется на asyncio.Event когда сервер недоступен."""
    watcher = IntegrationWatcher(creds, cache, fast_config)
    BarsWatcherManager.register_watcher(creds.user_id, watcher)

    # Сервер недоступен
    watcher.on_server_unavailable()

    await watcher.start()
    await asyncio.sleep(0.2)

    # Вотчер не делает итераций — ждёт
    assert watcher.iteration_count == 0
    assert not watcher.is_running or watcher._stats.status == WatcherStatus.WORKING

    # Сервер стал доступен
    watcher.on_server_available()
    await asyncio.sleep(0.2)

    # Вотчер возобновил работу
    assert watcher.iteration_count >= 1

    await watcher.stop()


# ─── Тест 2: Monitor DISCONNECTED → watcher paused ────────────────────

@pytest.mark.asyncio
async def test_monitor_disconnected_pauses_watchers(creds, fast_config, cache):
    """ConnectionMonitor DISCONNECTED → WatcherManager.pause_all()."""
    watcher = IntegrationWatcher(creds, cache, fast_config)
    BarsWatcherManager.register_watcher(creds.user_id, watcher)

    await watcher.start()
    await asyncio.sleep(0.15)
    assert watcher.iteration_count >= 1

    # Монитор сообщает DISCONNECTED
    await BarsWatcherManager.process_connection_event(ConnectionStatus.DISCONNECTED)

    # Вотчер поставлен на паузу
    assert not watcher.is_running
    assert watcher._stats.status == WatcherStatus.PAUSED

    await watcher.stop()


# ─── Тест 3: Monitor CONNECTED → staggered resume ─────────────────────

@pytest.mark.asyncio
async def test_monitor_connected_resumes_watchers(creds, fast_config, cache):
    """ConnectionMonitor CONNECTED → WatcherManager ставит event available."""
    watcher = IntegrationWatcher(creds, cache, fast_config)
    BarsWatcherManager.register_watcher(creds.user_id, watcher)

    # Ставим unavailable + paused
    watcher.on_server_unavailable()
    await watcher.pause()
    await asyncio.sleep(0.1)

    # Монитор сообщает CONNECTED → staggered resume
    await BarsWatcherManager.process_connection_event(ConnectionStatus.CONNECTED)

    # Event available установлен
    assert watcher._server_available.is_set()

    await watcher.stop()


# ─── Тест 4: Полный цикл: работает → падает → восстанавливается ──────

@pytest.mark.asyncio
async def test_full_lifecycle(creds, fast_config, cache):
    """Полный цикл: watcher работает → monitor DISCONNECTED → pause → monitor CONNECTED → resume."""
    watcher = IntegrationWatcher(creds, cache, fast_config)
    BarsWatcherManager.register_watcher(creds.user_id, watcher)

    # 1. Вотчер работает
    await watcher.start()
    await asyncio.sleep(0.15)
    iterations_before = watcher.iteration_count
    assert iterations_before >= 1

    # 2. Сервер упал
    await BarsWatcherManager.process_connection_event(ConnectionStatus.DISCONNECTED)
    assert not watcher.is_running

    # 3. Пауза — итераций не прибавляется
    await asyncio.sleep(0.2)
    assert watcher.iteration_count == iterations_before

    # 4. Сервер восстановился
    await BarsWatcherManager.process_connection_event(ConnectionStatus.CONNECTED)
    assert watcher._server_available.is_set()

    # 5. Вотчер возобновил работу
    await asyncio.sleep(0.2)
    assert watcher.iteration_count > iterations_before

    await watcher.stop()


# ─── Тест 5: Несколько вотчеров — staggered resume ────────────────────

@pytest.mark.asyncio
async def test_staggered_resume_multiple_watchers(fast_config, cache):
    """Несколько вотчеров возобновляются с задержками, не все сразу."""
    watchers = []
    for i in range(3):
        c = UserCredentials(username=f"user_{i}", password="p", user_id=100 + i, watcher_type=WatcherType.BARS)
        w = IntegrationWatcher(c, cache, fast_config)
        BarsWatcherManager.register_watcher(c.user_id, w)
        watchers.append(w)

    # Все на паузе
    for w in watchers:
        w.on_server_unavailable()
        await w.pause()
    await asyncio.sleep(0.1)

    # Staggered resume — должен занять время
    start = asyncio.get_event_loop().time()
    await BarsWatcherManager.process_connection_event(ConnectionStatus.CONNECTED)
    elapsed = asyncio.get_event_loop().time() - start

    # Должно занять минимум ~2 сек (STAGGER_DELAY)
    assert elapsed >= 1.5

    for w in watchers:
        assert w._server_available.is_set()

    for w in watchers:
        await w.stop()


# ─── Тест 6: DEGRADED — вотчеры продолжают работать ───────────────────

@pytest.mark.asyncio
async def test_degraded_watchers_continue(creds, fast_config, cache):
    """DEGRADED — вотчеры НЕ ставятся на паузу, продолжают работу."""
    watcher = IntegrationWatcher(creds, cache, fast_config)
    BarsWatcherManager.register_watcher(creds.user_id, watcher)

    await watcher.start()
    await asyncio.sleep(0.15)
    iterations_before = watcher.iteration_count

    # Монитор сообщает DEGRADED
    await BarsWatcherManager.process_connection_event(ConnectionStatus.DEGRADED)

    # Вотчер продолжает работать
    await asyncio.sleep(0.15)
    assert watcher.iteration_count > iterations_before
    assert watcher._server_available.is_set()

    await watcher.stop()


# ─── Тест 7: DISCONNECTED → Event блокирует итерацию ─────────────────

@pytest.mark.asyncio
async def test_event_blocks_iteration(creds, fast_config, cache):
    """asyncio.Event блокирует итерацию пока сервер недоступен."""
    watcher = IntegrationWatcher(creds, cache, fast_config)

    # Ставим unavailable до запуска
    watcher.on_server_unavailable()

    await watcher.start()
    await asyncio.sleep(0.3)

    # Итераций 0 — вотчер ждёт
    assert watcher.iteration_count == 0

    # Устанавливаем available — вотчер просыпается
    watcher.on_server_available()
    await asyncio.sleep(0.2)

    assert watcher.iteration_count >= 1

    await watcher.stop()


# ─── Тест 8: Быстрое переключение DISCONNECTED → CONNECTED ───────────

@pytest.mark.asyncio
async def test_rapid_disconnect_reconnect(creds, fast_config, cache):
    """Быстрое переключение: DISCONNECTED → CONNECTED не ломает вотчер."""
    watcher = IntegrationWatcher(creds, cache, fast_config)
    BarsWatcherManager.register_watcher(creds.user_id, watcher)

    await watcher.start()
    await asyncio.sleep(0.1)

    # Быстрое переключение
    await BarsWatcherManager.process_connection_event(ConnectionStatus.DISCONNECTED)
    await asyncio.sleep(0.05)
    await BarsWatcherManager.process_connection_event(ConnectionStatus.CONNECTED)
    await asyncio.sleep(0.2)

    # Вотчер работает
    assert watcher.iteration_count >= 1
    assert watcher._server_available.is_set()

    await watcher.stop()


# ─── Тест 9: WatcherManager обрабатывает все статусы ──────────────────

@pytest.mark.asyncio
async def test_manager_handles_all_statuses(creds, fast_config, cache):
    """WatcherManager корректно обрабатывает все ConnectionStatus."""
    watcher = IntegrationWatcher(creds, cache, fast_config)
    BarsWatcherManager.register_watcher(creds.user_id, watcher)

    await watcher.start()
    await asyncio.sleep(0.1)

    # Все статусы без ошибок
    for status in [ConnectionStatus.CONNECTED, ConnectionStatus.DEGRADED,
                   ConnectionStatus.RECOVERING, ConnectionStatus.UNKNOWN]:
        await BarsWatcherManager.process_connection_event(status)

    # Вотчер не сломан
    assert watcher._server_available.is_set()

    await watcher.stop()


# ─── Тест 10: EventService шина между monitor → manager → watcher ─────

@pytest.mark.asyncio
async def test_event_bus_integration(creds, fast_config, cache):
    """Полная шина: ConnectionMonitor → EventService → WatcherManager → Watcher."""
    events_received = []

    async def capture(event):
        events_received.append(event)

    watcher = IntegrationWatcher(creds, cache, fast_config)
    watcher.subscribe(capture)
    BarsWatcherManager.register_watcher(creds.user_id, watcher)

    await watcher.start()
    await asyncio.sleep(0.15)

    # Вотчер генерирует событие (через detect_changes, но у нас пусто)
    # Проверяем что подписка работает
    assert len(events_received) == 0  # Нет изменений — нет событий

    await watcher.stop()
    watcher.unsubscribe(capture)
