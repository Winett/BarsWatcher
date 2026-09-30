"""Порты интеграций: контракты клиентов БАРС и ОСЭП.

Реальные клиенты (web-интерфейс БАРС, OWA service.svc для ОСЭП) реализуют
эти протоколы; для разработки/тестов — sim-реализации (см. integrations.sim).

Ошибки намеренно разделены:
  - ServiceUnavailableError — сервис упал/недоступен (сеть, 5xx, таймаут) —
    НЕ ошибка авторизации, ретраи и circuit breaker;
  - AuthError — авторизация/сессия не удалась (сессия мертва, перелогин
    не удался);
  - TwoFactorRequiredError — перелогин потребовал 2FA (подкласс AuthError);
  - DataParseError — неожиданный формат ответа (парсер не разобрал страницу).
Неверный логин/пароль — не исключение, а AuthStatus.INVALID_CREDENTIALS.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from .models import (
    AbsenceRecord,
    DisciplineMarks,
    MailMessage,
    StudentProfile,
    TwoFASendResult,
)

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
]


class AuthStatus(StrEnum):
    OK = "ok"
    INVALID_CREDENTIALS = "invalid_credentials"
    TWO_FACTOR_REQUIRED = "two_factor_required"
    CODE_INVALID = "code_invalid"


@dataclass
class AuthResult:
    status: AuthStatus
    # для БАРС: профиль (если удалось получить заодно)
    profile: StudentProfile | None = None
    # причина отказа (текст ошибки сервера / диагностика), если есть
    error: str | None = None


class ServiceUnavailableError(Exception):
    """Сервис упал/недоступен (сеть/5xx/timeout) — не ошибка авторизации."""


class AuthError(Exception):
    """Сессия мертва, а повторная авторизация не удалась."""


class TwoFactorRequiredError(AuthError):
    """Перелогин потребовал 2FA — пользователю нужно заново ввести код."""


class DataParseError(Exception):
    """Неожиданный формат ответа — парсер не разобрал страницу."""


# Служебные/исходящие папки ОСЭП, которые не обрабатываем (как в master).
OSEP_IGNORE_FOLDERS = frozenset(
    {
        "Журнал",
        "Задачи",
        "Заметки",
        "Исходящие",
        "Нежелательная почта",
        "Отправленные",
        "Удаленные",
        "Удалённые",
        "Черновики",
        "Conversation Action Settings",
        "Working Set",
        "{06967759-274D-40B2-A3EB-D7F9E73727D7}",
        "{A9E2BC46-B3A0-4243-B315-60D991004455}",
        "GAL Contacts",
        "Recipient Cache",
    }
)


@runtime_checkable
class BarsClient(Protocol):
    """Клиент БАРС: авторизация (с 2FA), профиль, оценки, пропуски."""

    async def is_authenticated(self) -> bool: ...

    async def authenticate(self, login: str, password: str) -> AuthResult: ...

    async def send_2fa_code(self) -> TwoFASendResult:
        """Отправить код 2FA (destination — «Сообщение отправлено в ...»)."""
        ...

    async def verify_2fa_code(self, code: str) -> AuthResult:
        """Проверить введённый код: OK / CODE_INVALID."""
        ...

    async def fetch_profile(self) -> StudentProfile:
        """ФИО, группа, ответственный за журнал."""
        ...

    async def fetch_marks(self) -> dict[str, DisciplineMarks]:
        """Оценки: {дисциплина: DisciplineMarks} (КМ-1..N, переписывания, ПА)."""
        ...

    async def fetch_absences(self) -> dict[str, AbsenceRecord]:
        """Пропуски: {ключ записи: AbsenceRecord}."""
        ...

    async def check_availability(self) -> bool: ...

    def export_session(self) -> dict:
        """{"cookies": {...}, "pending_2fa": {...} | None} — для сохранения в БД."""
        ...

    def restore_session(self, session: dict | None) -> None: ...


@runtime_checkable
class OsepClient(Protocol):
    """Клиент ОСЭП (OWA): авторизация, папки, новые письма."""

    async def is_authenticated(self) -> bool: ...

    async def authenticate(self, login: str, password: str) -> AuthResult: ...

    async def fetch_folder_counts(self) -> dict[str, int]:
        """{имя папки: total_count}"""
        ...

    async def fetch_new_mails(self, folder: str, count: int) -> list[MailMessage]:
        """Новые письма папки (от новых к старым)."""
        ...

    async def download_attachment(self, attachment_id: str) -> bytes:
        """Скачать вложение целиком (сырые байты)."""
        ...

    async def check_availability(self) -> bool: ...

    def export_session(self) -> dict:
        """{"cookies": {...}, "pending_2fa": {...} | None}"""
        ...

    def restore_session(self, session: dict | None) -> None: ...
