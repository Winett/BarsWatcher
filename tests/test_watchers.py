import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from watchers.core.base_watcher import BaseWatcher
from watchers.core.event_service import EventService
from watchers.core.exceptions import AuthError, Auth2FA, DataParsingError
from watchers.models.watcher_models import (
    UserCredentials, WatcherConfig, WatcherType,
    WatcherEvent, WatcherStatus, EventType
)
from watchers.services.redis_cache_service import RedisCacheService


# ─── Concrete test watcher ────────────────────────────────────────────

class MockWatcher(BaseWatcher):
    """Тестовый вотчер для проверки BaseWatcher."""

    def __init__(self, credentials, cache_service, config=None,
                 fetch_return=None, fetch_side_effect=None,
                 process_return=None, detect_return=None):
        super().__init__(credentials, cache_service, config)
        self.fetch_return = fetch_return
        self.fetch_side_effect = fetch_side_effect
        self.process_return = process_return if process_return is not None else fetch_return
        self.detect_return = detect_return if detect_return is not None else []
        self.fetch_call_count = 0

    def _register_instance(self):
        pass  # Не регистрируем в менеджере для тестов

    async def fetch_data(self):
        self.fetch_call_count += 1
        if self.fetch_side_effect:
            raise self.fetch_side_effect
        return self.fetch_return

    async def process_data(self, data):
        return self.process_return

    async def detect_changes(self, old_data, new_data):
        return self.detect_return


# ─── Fixtures ─────────────────────────────────────────────────────────

@pytest.fixture
def creds():
    return UserCredentials(
        username="test_user",
        password="test_pass",
        user_id=12345,
        watcher_type=WatcherType.BARS
    )


@pytest.fixture
def fast_config():
    return WatcherConfig(poll_interval=0.1)


@pytest.fixture
def cache():
    mock = AsyncMock(spec=RedisCacheService)
    mock.get.return_value = None
    mock.set.return_value = None
    mock.delete.return_value = True
    mock.exists.return_value = False
    return mock


# ─── Тест 1: Запуск вотчера, работает до ручной остановки ─────────────

@pytest.mark.asyncio
async def test_watcher_runs_until_manual_stop(creds, fast_config, cache):
    """Вотчер запускается, работает в цикле, и корректно останавливается вручную."""
    watcher = MockWatcher(
        creds, cache, fast_config,
        fetch_return="test_data",
        detect_return=[WatcherEvent(
            event_type=EventType.MARK_CHANGED,
            user_id=12345,
            username="test_user",
            status=WatcherStatus.WORKING,
            watcher_type=WatcherType.BARS,
            message="change1"
        )]
    )

    await watcher.start()
    assert watcher._task is not None
    assert not watcher._task.done()

    # Даём поработать ~3 итерации
    await asyncio.sleep(0.4)

    assert watcher.is_running
    assert watcher.fetch_call_count >= 2
    assert watcher.stats.status == WatcherStatus.WORKING

    # Ручная остановка
    await watcher.stop()
    await asyncio.sleep(0.05)

    assert not watcher.is_running
    assert watcher._task is None
    assert watcher.stats.status == WatcherStatus.STOPPED


# ─── Тест 2: Сетевая ошибка — вотчер продолжает работать ──────────────

@pytest.mark.asyncio
async def test_watcher_survives_network_error(creds, fast_config, cache):
    """При сетевой ошибке вотчер ловит исключение, логирует и продолжает цикл."""
    call_count = 0

    async def flaky_fetch():
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise ConnectionError("Simulated network error")
        return "ok"

    watcher = MockWatcher(creds, cache, fast_config)
    watcher.fetch_return = None
    # Подменяем fetch_data на flaky
    watcher.fetch_data = flaky_fetch

    await watcher.start()
    # Ждём пока пройдут обе итерации (вторая вызовет ошибку, третья — успех)
    await asyncio.sleep(0.5)

    assert watcher.is_running
    assert call_count >= 3  # Успешная + ошибка + ещё минимум одна
    assert watcher.stats.error_count >= 1

    await watcher.stop()


# ─── Тест 3: Auth ошибка / 2FA — уведомление + остановка ─────────────

@pytest.mark.asyncio
async def test_watcher_stops_on_auth_error(creds, fast_config, cache):
    """При ошибке авторизации вотчер уведомляет и останавливается."""
    events_received = []

    async def capture_event(event: WatcherEvent):
        events_received.append(event)

    watcher = MockWatcher(
        creds, cache, fast_config,
        fetch_side_effect=AuthError("Invalid credentials")
    )
    watcher.subscribe(capture_event)

    await watcher.start()
    # Ждём пока ошибка обработается
    await asyncio.sleep(0.3)

    # Вотчер должен остановиться (run() поймал ошибку и вышел)
    assert not watcher.is_running
    assert watcher.stats.status == WatcherStatus.STOPPED

    # Должно быть получено событие EXCEPTION
    exc_events = [e for e in events_received if e.event_type == EventType.EXCEPTION]
    assert len(exc_events) >= 1
    assert isinstance(exc_events[0].error, AuthError)

    await watcher.stop()


@pytest.mark.asyncio
async def test_watcher_stops_on_2fa_required(creds, fast_config, cache):
    """При требовании 2FA вотчер уведомляет и останавливается."""
    events_received = []

    async def capture_event(event: WatcherEvent):
        events_received.append(event)

    watcher = MockWatcher(
        creds, cache, fast_config,
        fetch_side_effect=Auth2FA("2FA required")
    )
    watcher.subscribe(capture_event)

    await watcher.start()
    await asyncio.sleep(0.3)

    assert not watcher.is_running
    exc_events = [e for e in events_received if e.event_type == EventType.EXCEPTION]
    assert len(exc_events) >= 1
    assert isinstance(exc_events[0].error, Auth2FA)


# ─── Тест 4: Ошибка парсинга — вотчер продолжает + контент админу ────

@pytest.mark.asyncio
async def test_watcher_survives_parsing_error(creds, fast_config, cache):
    """При ошибке парсинга вотчер продолжает работать, контент отправляется админу."""
    events_received = []
    call_count = 0

    async def capture_event(event: WatcherEvent):
        events_received.append(event)

    async def flaky_process(data):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise DataParsingError("Parse error", content="bad_html_content")
        return data

    watcher = MockWatcher(creds, cache, fast_config, fetch_return="data")
    watcher.process_data = flaky_process
    watcher.subscribe(capture_event)

    await watcher.start()
    await asyncio.sleep(0.5)

    # Вотчер продолжает работать
    assert watcher.is_running
    assert watcher.fetch_call_count >= 2

    # Есть событие ошибки парсинга
    exc_events = [e for e in events_received if e.event_type == EventType.EXCEPTION]
    assert len(exc_events) >= 1
    assert isinstance(exc_events[0].error, DataParsingError)

    await watcher.stop()


# ─── Дополнительные тесты ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_watcher_pause_resume(creds, fast_config, cache):
    """Пауза и возобновление вотчера."""
    watcher = MockWatcher(creds, cache, fast_config, fetch_return="data")

    await watcher.start()
    await asyncio.sleep(0.2)
    assert watcher.is_running

    await watcher.pause()
    assert not watcher.is_running
    assert watcher.stats.status == WatcherStatus.PAUSED

    await watcher.resume()
    await asyncio.sleep(0.2)
    assert watcher.is_running
    assert watcher.stats.status == WatcherStatus.WORKING

    await watcher.stop()


@pytest.mark.asyncio
async def test_watcher_restart(creds, fast_config, cache):
    """Перезапуск вотчера."""
    watcher = MockWatcher(creds, cache, fast_config, fetch_return="data")

    await watcher.start()
    await asyncio.sleep(0.2)
    old_task = watcher._task

    await watcher.restart()
    await asyncio.sleep(0.2)

    assert watcher._task is not old_task
    assert watcher.is_running

    await watcher.stop()


@pytest.mark.asyncio
async def test_watcher_double_start_is_noop(creds, fast_config, cache):
    """Повторный start() ничего не делает."""
    watcher = MockWatcher(creds, cache, fast_config, fetch_return="data")

    await watcher.start()
    task1 = watcher._task
    await asyncio.sleep(0.1)

    await watcher.start()  # Должен вернуться сразу
    task2 = watcher._task

    assert task1 is task2  # Та же задача

    await watcher.stop()


@pytest.mark.asyncio
async def test_event_service_singleton():
    """EventService — синглтон."""
    es1 = EventService()
    es2 = EventService()
    assert es1 is es2


@pytest.mark.asyncio
async def test_watcher_detects_changes_and_notifies(creds, fast_config, cache):
    """Вотчер обнаруживает изменения и отправляет уведомления."""
    events_received = []

    async def capture_event(event: WatcherEvent):
        events_received.append(event)

    watcher = MockWatcher(
        creds, cache, fast_config,
        fetch_return="data",
        detect_return=[WatcherEvent(
            event_type=EventType.MARK_CHANGED,
            user_id=12345,
            username="test_user",
            status=WatcherStatus.WORKING,
            watcher_type=WatcherType.BARS,
            message="Изменилась оценка по Математике КМ-1: 4 → 5",
            subject="Математика"
        )]
    )
    watcher.subscribe(capture_event)

    await watcher.start()
    await asyncio.sleep(0.3)

    change_events = [e for e in events_received if e.event_type == EventType.MARK_CHANGED]
    assert len(change_events) >= 1
    assert "Математике" in change_events[0].message

    await watcher.stop()
