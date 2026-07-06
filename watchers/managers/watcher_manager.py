import asyncio
import random
from typing import Dict, Optional, Generic, TypeVar
from abc import ABC
from collections import defaultdict

from loguru import logger

from watchers.core.event_service import EventService
from watchers.core.base_watcher import BaseWatcher
from watchers.models.watcher_models import (
    UserCredentials, WatcherType, WatcherEvent, EventType, WatcherStatus
)
from watchers.models.connection_monitor_models import ConnectionStatus
from watchers.models.mail_models import AttachmentData
from watchers.services.notification_service import TelegramNotificationService
from watchers.core.exceptions import AuthError, DataParsingError, ResponseError, RequestVerificationTokenError, Auth2FA

from services.user import UserService
from database.db import async_session
from settings import settings
from uuid import uuid4
from html import escape as html_escape

W = TypeVar('W', bound=BaseWatcher)


class WatcherManager(ABC, Generic[W]):
    """Единый менеджер вотчеров с generic-типизацией."""

    _managers_watchers: dict[str, dict[int, BaseWatcher]] = defaultdict(dict)
    _pending_watchers: dict[str, dict[int, dict]] = defaultdict(dict)
    notification_service = TelegramNotificationService()
    _config_service = None

    # Конфигурация staggered resume (дефолты, если нет GlobalConfig)
    STAGGER_DELAY = 2.0    # Базовая задержка между вотчерами (сек)
    STAGGER_JITTER = 3.0   # Случайный разброс (сек)

    @classmethod
    def set_config_service(cls, config_service):
        """Установить ConfigService для менеджера."""
        cls._config_service = config_service

    @classmethod
    def _get_watcher_type(cls) -> Optional[WatcherType]:
        """Тип вотчеров, которые обрабатывает этот менеджер. None = все типы."""
        return None

    @classmethod
    def _get_watchers(cls) -> dict[int, W]:
        return cls._managers_watchers[cls.__name__]

    @classmethod
    def _get_pending(cls) -> dict[int, dict]:
        return cls._pending_watchers[cls.__name__]

    @classmethod
    async def process_connection_event(cls, conn_event: ConnectionStatus):
        """Обработка события соединения от ConnectionMonitor."""
        logger.info(f"{cls.__name__} | Событие соединения: {conn_event.value}")

        match conn_event:
            case ConnectionStatus.CONNECTED:
                # Сервер восстановился — staggered resume
                await cls._staggered_resume()
                # Обработать очередь ожидающих вотчеров
                await cls._process_pending_watchers()
            case ConnectionStatus.DEGRADED:
                # Сервер медленный — просто логируем, вотчеры продолжают
                logger.warning(f"{cls.__name__} | Сервер работает медленно (DEGRADED)")
            case ConnectionStatus.DISCONNECTED:
                # Сервер недоступен — ставим event unavailable + пауза
                cls._set_all_server_unavailable()
                await cls.pause_all()
            case ConnectionStatus.RECOVERING:
                # Сервер восстанавливается — обработать очередь
                logger.info(f"{cls.__name__} | Сервер восстанавливается (RECOVERING)")
                await cls._process_pending_watchers()
            case _:
                logger.debug(f"{cls.__name__} | Статус: {conn_event.value}")

    @classmethod
    def add_pending_watcher(cls, user_id: int, credentials: dict):
        """Добавить вотчер в очередь ожидания (при недоступности сервера)."""
        pending = cls._get_pending()
        pending[user_id] = credentials
        logger.info(f"{cls.__name__} | Вотчер {user_id} добавлен в очередь ожидания ({len(pending)} в очереди)")

    @classmethod
    def remove_pending_watcher(cls, user_id: int):
        """Убрать вотчер из очереди ожидания."""
        cls._get_pending().pop(user_id, None)

    @classmethod
    async def _process_pending_watchers(cls):
        """Обработать очередь ожидающих вотчеров при восстановлении соединения."""
        pending = cls._get_pending()
        if not pending:
            return

        logger.info(f"{cls.__name__} | Обработка очереди: {len(pending)} вотчеров")
        user_ids = list(pending.keys())

        for user_id in user_ids:
            creds_data = pending.pop(user_id)
            try:
                await cls._create_and_start_from_pending(user_id, creds_data)
            except Exception as e:
                logger.error(f"{cls.__name__} | Ошибка запуска вотчер {user_id} из очереди: {e}")

    @classmethod
    async def _create_and_start_from_pending(cls, user_id: int, creds_data: dict):
        """Создать и запустить вотчер из данных очереди (реализуется в подклассах)."""
        pass

    @classmethod
    def _set_all_server_unavailable(cls):
        """Установить event unavailable для всех вотчеров."""
        for watcher in cls._get_watchers().values():
            watcher.on_server_unavailable()

    @classmethod
    def _set_all_server_available(cls):
        """Установить event available для всех вотчеров."""
        for watcher in cls._get_watchers().values():
            watcher.on_server_available()

    @classmethod
    async def _staggered_resume(cls):
        """Возобновление вотчеров с задержками для предотвращения thundering herd."""
        watchers = list(cls._get_watchers().values())
        if not watchers:
            return

        # Сначала ставим event available
        cls._set_all_server_available()

        # Читаем stagger_delay и stagger_jitter из GlobalConfig (или дефолты)
        stagger_delay = cls.STAGGER_DELAY
        stagger_jitter = cls.STAGGER_JITTER
        if cls._config_service:
            try:
                global_cfg = await cls._config_service.get_global()
                stagger_delay = global_cfg.stagger_delay
                stagger_jitter = global_cfg.stagger_jitter
            except Exception as e:
                logger.warning(f"{cls.__name__} | Не удалось загрузить stagger config: {e}")

        # Случайный порядок для равномерного распределения
        random.shuffle(watchers)

        logger.info(f"{cls.__name__} | Staggered resume: {len(watchers)} вотчеров | delay={stagger_delay}s jitter={stagger_jitter}s")

        for i, watcher in enumerate(watchers):
            delay = stagger_delay + random.uniform(0, stagger_jitter)
            logger.info(
                f"{cls.__name__} | Resume {watcher.credentials.username} "
                f"через {delay:.1f}s ({i+1}/{len(watchers)})"
            )
            await asyncio.sleep(delay)
            await watcher.resume()

        logger.info(f"{cls.__name__} | Staggered resume завершён")

    @classmethod
    def register_watcher(cls, user_id: int, watcher: W):
        cls._get_watchers()[user_id] = watcher
        watcher.subscribe(cls._handle_watcher_event)

    @classmethod
    async def register_watcher_and_start(cls, user_id: int, watcher: W):
        cls.register_watcher(user_id, watcher)
        await watcher.start()

    @classmethod
    def unregister_watcher(cls, user_id: int):
        watcher = cls._get_watchers().pop(user_id, None)
        if watcher:
            watcher.unsubscribe(cls._handle_watcher_event)

    @classmethod
    def get_watcher_instance(cls, user_id: int) -> Optional[W]:
        return cls._get_watchers().get(user_id, None)

    @classmethod
    async def _handle_watcher_event(cls, event: WatcherEvent):
        """Обработка событий от вотчеров"""
        # Пропуск событий от другого типа вотчеров (BarsManager обрабатывает только BARS, OsepManager — только OSEP)
        expected_type = cls._get_watcher_type()
        if expected_type and event.watcher_type != expected_type:
            return

        logger.info(
            f"{cls.__name__} | {event.username} ({event.user_id}) | "
            f"Событие: {event.event_type.value} | {event.watcher_type.value}"
        )
        match event.event_type:
            # === БАРС: Оценки ===
            case EventType.NEW_MARK:
                await cls._send_notification(event, "📝 Новая оценка")

            case EventType.MARK_CHANGED:
                await cls._send_notification(event, "🔄 Изменение оценки")

            case EventType.REWRITING:
                await cls._send_notification(event, "✏️ Переписывание")

            case EventType.FINAL_GRADE_CHANGED:
                await cls._send_notification(event, "🎓 Итоговая оценка")

            # === ОСЭП: Почта ===
            case EventType.NEW_MAIL:
                await cls._send_notification(event, "Новое письмо!")

            # === Системные ===
            case EventType.EXCEPTION:
                logger.error(
                    f"{cls.__name__} | {event.username} | "
                    f"Ошибка: {type(event.error).__name__}: {event.error}"
                )
                match event.error:
                    case error if isinstance(error, (AuthError, Auth2FA)):
                        if isinstance(error, Auth2FA):
                            message = "Нужно переавторизоваться"
                        else:
                            message = "Неверный логин или пароль"
                        logger.warning(f"{cls.__name__} | {event.username} | Фатальная ошибка: {message}")
                        await cls.notification_service.send_message(
                            event.user_id,
                            f" [{event.watcher_type.value}] {message}"
                        )
                        await cls.stop_and_delete(event.user_id)
                        async with async_session() as session:
                            if event.watcher_type == WatcherType.BARS:
                                await UserService(session).set_bars_status_used(event.user_id, False)
                            elif event.watcher_type == WatcherType.OSEP:
                                await UserService(session).set_osep_status_used(event.user_id, False)
                        logger.info(f"{cls.__name__} | {event.username} | Вотчер остановлен, статус сброшен")
                    case error:
                        if isinstance(error, (DataParsingError, ResponseError, RequestVerificationTokenError)):
                            uid = uuid4().hex
                            content = error.content.encode(errors="ignore", encoding="utf-8") if isinstance(error.content, str) else error.content
                            att_data = AttachmentData(
                                id=uid,
                                content_type="application/html",
                                filename=f"{event.username}_{event.user_id}_{event.watcher_type.value}.html",
                                size=len(content),
                                content=content,
                            )
                            logger.info(f"{cls.__name__} | {event.username} | Контент ошибки отправлен админу ({len(content)} bytes)")
                            for admin in settings.admins:
                                await cls.notification_service.send_message_with_documents(
                                    admin,
                                    f"Ошибка при обработке запроса: {type(error).__name__} у {event.username} <code>{event.user_id}</code>",
                                    files=[att_data]
                                )
                        logger.exception(error)
                        logger.info(f"{cls.__name__} | {event.username} | Перезапуск через 5 сек...")
                        await asyncio.sleep(5)
                        try:
                            await cls.get_watcher_instance(event.user_id).restart()
                            logger.info(f"{cls.__name__} | {event.username} | Вотчер перезапущен")
                        except AttributeError:
                            logger.warning(f"{cls.__name__} | {event.username} | Вотчер не найден для перезапуска")
            case _:
                logger.warning(f"{cls.__name__} Неизвестное событие: {event.event_type}")

    @classmethod
    async def _send_notification(cls, event: WatcherEvent, header: str):
        """Отправить уведомление с заголовком."""
        files = event.metadata.get('files', [])
        logger.info(f"{cls.__name__} | {event.username} | {header} | files={len(files)}")
        # Экранируем сообщение от вредоносного HTML (ссылки вида <https://...>)
        safe_message = html_escape(event.message)
        await cls.notification_service.send_message_with_documents(
            event.user_id,
            f"{header}\n\n{safe_message}",
            files=files
        )

    @classmethod
    def _escape_for_telegram(cls, text: str) -> str:
        """Экранировать HTML-сущности в тексте для безопасной отправки."""
        return html_escape(text)

    @classmethod
    def _get_all_not_started_instance(cls) -> list[W]:
        return [w for w in cls._get_watchers().values() if not w.is_running]

    @classmethod
    async def pause_all(cls):
        logger.info(f"{cls.__name__} | Пауза всех вотчеров")
        cnt = 0
        for w in cls._get_watchers().values():
            await w.pause()
            cnt += 1
        logger.info(f"{cls.__name__} | Пауза {cnt} вотчеров")

    @classmethod
    async def resume_all(cls):
        logger.info(f"{cls.__name__} | Возобновление всех вотчеров")
        cnt = 0
        for w in cls._get_watchers().values():
            await w.resume()
            cnt += 1
        logger.info(f"{cls.__name__} | Возобновление {cnt} вотчеров")

    @classmethod
    async def refresh_all_configs(cls):
        """Обновить конфигурацию у всех работающих вотчеров (после изменения настроек)."""
        logger.info(f"{cls.__name__} | Обновление конфигурации у {len(cls._get_watchers())} вотчеров")
        for watcher in cls._get_watchers().values():
            await watcher.refresh_config()

    @classmethod
    async def start_all(cls):
        instances = cls._get_all_not_started_instance()
        logger.debug(f"{cls.__name__} Запуск {len(instances)} вотчеров")
        for w in instances:
            await w.start()

    @classmethod
    async def stop_all(cls):
        for w in cls._get_watchers().values():
            await w.stop()

    @classmethod
    async def stop_and_delete(cls, user_id: int):
        watcher = cls.get_watcher_instance(user_id)
        if watcher:
            await watcher.stop()
            await watcher.close()
            cls.unregister_watcher(user_id)

    @classmethod
    def watcher_stats(cls):
        stats = {}
        cnt = 0

        watcher_status = defaultdict(int)
        non_running = defaultdict(list)
        for user_id, w in cls._get_watchers().items():
            watcher_status[w.stats.status.value] += 1
            if w.stats.status not in [WatcherStatus.WORKING]:
                non_running[w.stats.status.value].append(
                    (f"<code>{user_id}</code>", w.credentials.username)
                )
            cnt += 1
        stats = {
            "count": cnt,
            "watcher_status": watcher_status,
            "non_running": non_running,
        }
        return stats


class BarsWatcherManager(WatcherManager):
    @classmethod
    def _get_watcher_type(cls) -> Optional[WatcherType]:
        return WatcherType.BARS

    @classmethod
    async def _create_and_start_from_pending(cls, user_id: int, creds_data: dict):
        """Создать и запустить BarsWatcher из очереди ожидания."""
        from watchers.services.watcher_factory import WatcherFactory
        from watchers.models.watcher_models import WatcherType
        from watchers.core.exceptions import Auth2FA, AuthError
        from aiohttp import ClientError

        auth, session_obj = WatcherFactory.create_auth_and_session(
            user_id=user_id,
            service="bars",
            login=creds_data["login"],
            password=creds_data["password"],
            watcher_type=WatcherType.BARS
        )

        try:
            res = await auth.login()
        except Auth2FA:
            logger.warning(f"{cls.__name__} | {user_id} требует 2FA из очереди")
            await cls.notification_service.send_message(
                user_id, "[БАРС] Необходимо переавторизоваться"
            )
            async with async_session() as session:
                await UserService(session).set_bars_status_used(user_id, False)
            return
        except AuthError:
            logger.warning(f"{cls.__name__} | Неверные данные для {user_id} из очереди")
            await cls.notification_service.send_message(
                user_id, "[БАРС] Неверный логин или пароль. Отслеживание отключено."
            )
            async with async_session() as session:
                await UserService(session).set_bars_status_used(user_id, False)
            return
        except ClientError:
            logger.info(f"{cls.__name__} | Сервер БАРС всё ещё недоступен для {user_id}, возвращаем в очередь")
            cls.add_pending_watcher(user_id, creds_data)
            return

        if not res:
            await cls.notification_service.send_message(
                user_id, "[БАРС] Неверный логин или пароль. Отслеживание отключено."
            )
            async with async_session() as session:
                await UserService(session).set_bars_status_used(user_id, False)
            return

        watcher = await WatcherFactory.create_bars_watcher(user_id, auth)
        await watcher.start()
        logger.info(f"{cls.__name__} | Вотчер {user_id} запущен из очереди")


class OsepWatcherManager(WatcherManager):
    @classmethod
    def _get_watcher_type(cls) -> Optional[WatcherType]:
        return WatcherType.OSEP

    @classmethod
    async def _create_and_start_from_pending(cls, user_id: int, creds_data: dict):
        """Создать и запустить OsepWatcher из очереди ожидания."""
        from watchers.services.watcher_factory import WatcherFactory
        from watchers.models.watcher_models import WatcherType
        from aiohttp import ClientError

        auth, session_obj = WatcherFactory.create_auth_and_session(
            user_id=user_id,
            service="osep",
            login=creds_data["login"],
            password=creds_data["password"],
            watcher_type=WatcherType.OSEP
        )

        try:
            res = await auth.login()
        except ClientError:
            logger.info(f"{cls.__name__} | Сервер ОСЭП всё ещё недоступен для {user_id}, возвращаем в очередь")
            cls.add_pending_watcher(user_id, creds_data)
            return

        if not res:
            await cls.notification_service.send_message(
                user_id, "[ОСЭП] Неверный логин или пароль. Отслеживание отключено."
            )
            async with async_session() as session:
                await UserService(session).set_osep_status_used(user_id, False)
            return

        watcher = await WatcherFactory.create_osep_watcher(user_id, auth)
        await watcher.start()
        logger.info(f"{cls.__name__} | Вотчер {user_id} запущен из очереди")
