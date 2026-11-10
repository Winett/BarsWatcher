from collections import OrderedDict

from loguru import logger

from src.core.config import settings

from .models import (
    AbsenceRecord,
    Attachment,
    DisciplineMarks,
    LessonInfo,
    MailMessage,
    Mark,
    OsepFolder,
    RewritingMark,
    ScheduleItem,
    StudentBrief,
    StudentProfile,
    StudyGroupInfo,
    TwoFASendResult,
    absences_to_snapshot,
    marks_to_snapshot,
)
from .ports import (
    OSEP_IGNORE_FOLDERS,
    AuthError,
    AuthResult,
    AuthStatus,
    BarsClient,
    DataParseError,
    OsepClient,
    ServiceUnavailableError,
    TwoFactorRequiredError,
)
from .sim import SimBarsClient, SimOsepClient

__all__ = [
    "AuthStatus",
    "AuthResult",
    "ServiceUnavailableError",
    "AuthError",
    "TwoFactorRequiredError",
    "DataParseError",
    "BarsClient",
    "OsepClient",
    "OSEP_IGNORE_FOLDERS",
    "get_bars_client",
    "get_osep_client",
    # модели
    "Mark",
    "RewritingMark",
    "DisciplineMarks",
    "AbsenceRecord",
    "StudentBrief",
    "StudentProfile",
    "StudyGroupInfo",
    "LessonInfo",
    "ScheduleItem",
    "TwoFASendResult",
    "Attachment",
    "MailMessage",
    "OsepFolder",
    "marks_to_snapshot",
    "absences_to_snapshot",
]


# Кэш реальных клиентов (только real-режим): один httpx-клиент на пользователя.
# Раньше клиент создавался на КАЖДЫЙ вызов и никогда не закрывался — утечка
# сокетов/памяти (worker — каждая проверка, бот — каждое письмо). Клиент
# переживает проверки с тёплой сессией; при вытеснении закрывается. Сессии
# хранятся в БД и восстанавливаются через restore_session — вытеснение
# безопасно.
_CLIENTS: OrderedDict[tuple[str, int], BarsClient | OsepClient] = OrderedDict()
_CLIENTS_MAX = 100  # одновременно живых клиентов на процесс


def _cached_client(kind: str, user_id: int, factory) -> object:
    key = (kind, user_id)
    client = _CLIENTS.get(key)
    if client is not None:
        _CLIENTS.move_to_end(key)
        return client
    client = factory()
    _CLIENTS[key] = client
    while len(_CLIENTS) > _CLIENTS_MAX:
        _old_key, old = _CLIENTS.popitem(last=False)
        _close_later(old)
    return client


def _close_later(client) -> None:
    """Закрыть вытесненный клиент асинхронно (мы в sync-коде)."""
    import asyncio

    close = getattr(client, "close", None)
    if close is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # вне event loop (скрипты) — закроет GC
    task = loop.create_task(close())
    task.add_done_callback(
        lambda t: t.exception()
        and logger.warning("клиент не закрылся: {}", t.exception())
    )


def get_bars_client(user_id: int) -> BarsClient:
    if settings.integration_mode == "sim":
        return SimBarsClient(user_id)
    if settings.integration_mode == "file":
        from .file_sim import FileSimBarsClient

        return FileSimBarsClient(user_id)
    from .bars import BarsWebClient

    return _cached_client(
        "bars", user_id, lambda: BarsWebClient(timeout=settings.request_timeout_seconds)
    )


def get_osep_client(user_id: int) -> OsepClient:
    if settings.integration_mode == "sim":
        return SimOsepClient(user_id)
    if settings.integration_mode == "file":
        from .file_sim import FileSimOsepClient

        return FileSimOsepClient(user_id)
    from .osep import OsepWebClient

    return _cached_client(
        "osep", user_id, lambda: OsepWebClient(timeout=settings.request_timeout_seconds)
    )
