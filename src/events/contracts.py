"""Контракты событий.

Одно событие = задача в очереди (arq). Класс события декларирует:
  - event_type — имя для реестра обработчиков и десериализации;
  - target_queue — в какую очередь публиковать:
      QUEUE_WORKER — проверки данных (обрабатывает worker),
      QUEUE_BOT    — уведомления/команды (обрабатывает bot).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from typing import ClassVar

from src.db.models.enums import ServiceKind

QUEUE_WORKER = "worker"
QUEUE_BOT = "bot"

__all__ = [
    "Event",
    "ServiceKind",
    "QUEUE_WORKER",
    "QUEUE_BOT",
    "EVENT_TYPES",
    "PollDue",
    "CheckRequested",
    "MarksChanged",
    "AbsencesChanged",
    "MailReceived",
    "ServiceStatusChanged",
    "Auth2FARequired",
    "Auth2FACodeResent",
    "Auth2FASubmitted",
    "Auth2FAResendRequested",
    "AuthCompleted",
    "Auth2FAFailed",
    "AuthFailed",
    "ReminderDue",
    "LessonsCheckRequested",
    "LessonsCheckResult",
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(kw_only=True)
class Event:
    """Базовое событие. Поля event_id/occurred_at — идемпотентность и аудит."""

    event_type: ClassVar[str] = "event"
    target_queue: ClassVar[str] = QUEUE_WORKER

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: str = field(default_factory=_now_iso)

    def to_payload(self) -> dict:
        data = asdict(self)
        data["event_type"] = self.event_type
        return data

    @classmethod
    def from_payload(cls, payload: dict) -> Event:
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in payload.items() if k in allowed})


# ─────────────────────────── события worker-очереди ───────────────────────────


@dataclass(kw_only=True)
class PollDue(Event):
    """Планировщик: пора проверить данные пользователя в сервисе."""

    event_type: ClassVar[str] = "poll.due"
    user_id: int
    service: ServiceKind


@dataclass(kw_only=True)
class CheckRequested(Event):
    """Внешняя команда «проверить сейчас» (от бота)."""

    event_type: ClassVar[str] = "check.requested"
    target_queue: ClassVar[str] = QUEUE_WORKER
    user_id: int
    service: ServiceKind


# ─────────────────────────── события bot-очереди ───────────────────────────


@dataclass(kw_only=True)
class MarksChanged(Event):
    """Оценки изменились. changes: [{subject, kind, km, old, new, date}]."""

    event_type: ClassVar[str] = "marks.changed"
    target_queue: ClassVar[str] = QUEUE_BOT
    user_id: int
    changes: list[dict]


@dataclass(kw_only=True)
class AbsencesChanged(Event):
    """Пропуски изменились. changes: [{kind, key, lesson, created, changed}]."""

    event_type: ClassVar[str] = "absences.changed"
    target_queue: ClassVar[str] = QUEUE_BOT
    user_id: int
    changes: list[dict]


@dataclass(kw_only=True)
class MailReceived(Event):
    """Новое письмо в ОСЭП."""

    event_type: ClassVar[str] = "mail.received"
    target_queue: ClassVar[str] = QUEUE_BOT
    user_id: int
    folder: str
    mail: dict  # {subject, sender_name, sender_email, body, attachments: [...]}


@dataclass(kw_only=True)
class ServiceStatusChanged(Event):
    """Сервис сменил доступность. since — с какого момента новый статус."""

    event_type: ClassVar[str] = "service.status_changed"
    target_queue: ClassVar[str] = QUEUE_BOT
    service: ServiceKind
    is_up: bool
    since: str | None = None


@dataclass(kw_only=True)
class Auth2FARequired(Event):
    """Сервис требует 2FA: бот должен спросить код у пользователя.

    destination — куда сервис отправил код («Сообщение отправлено в 'ВКонтакте'»).
    """

    event_type: ClassVar[str] = "auth.2fa_required"
    target_queue: ClassVar[str] = QUEUE_BOT
    user_id: int
    service: ServiceKind
    destination: str | None = None


@dataclass(kw_only=True)
class Auth2FACodeResent(Event):
    """Код 2FA отправлен пользователю повторно."""

    event_type: ClassVar[str] = "auth.2fa_code_resent"
    target_queue: ClassVar[str] = QUEUE_BOT
    user_id: int
    service: ServiceKind
    destination: str | None = None


@dataclass(kw_only=True)
class Auth2FASubmitted(Event):
    """Пользователь ввёл код 2FA (от бота) — worker проверяет его."""

    event_type: ClassVar[str] = "auth.2fa_submitted"
    target_queue: ClassVar[str] = QUEUE_WORKER
    user_id: int
    service: ServiceKind
    code: str


@dataclass(kw_only=True)
class Auth2FAResendRequested(Event):
    """Кнопка «Отправить код снова» (от бота) — worker отправляет код."""

    event_type: ClassVar[str] = "auth.2fa_resend_requested"
    target_queue: ClassVar[str] = QUEUE_WORKER
    user_id: int
    service: ServiceKind


@dataclass(kw_only=True)
class AuthCompleted(Event):
    """Авторизация завершена (в т.ч. после 2FA) — можно включать отслеживание."""

    event_type: ClassVar[str] = "auth.completed"
    target_queue: ClassVar[str] = QUEUE_BOT
    user_id: int
    service: ServiceKind


@dataclass(kw_only=True)
class Auth2FAFailed(Event):
    """Код 2FA введён неверно — бот предлагает повторить ввод/отправить снова."""

    event_type: ClassVar[str] = "auth.2fa_failed"
    target_queue: ClassVar[str] = QUEUE_BOT
    user_id: int
    service: ServiceKind


@dataclass(kw_only=True)
class AuthFailed(Event):
    """Авторизация не удалась (неверный логин/пароль)."""

    event_type: ClassVar[str] = "auth.failed"
    target_queue: ClassVar[str] = QUEUE_BOT
    user_id: int
    service: ServiceKind
    reason: str = ""


@dataclass(kw_only=True)
class ReminderDue(Event):
    """Напоминание о проставлении пар (для ответственного за журнал).

    issues — детали «что не так» с парами: [{kind, date_label, pair, time,
    subject, lesson_kind, teacher, status, reason}] (см. src/services/lessons.py).
    """

    event_type: ClassVar[str] = "reminder.due"
    target_queue: ClassVar[str] = QUEUE_BOT
    user_id: int
    issues: list[dict] = field(default_factory=list)


@dataclass(kw_only=True)
class LessonsCheckRequested(Event):
    """Команда «сверить пары журнала с расписанием» (от ответственного)."""

    event_type: ClassVar[str] = "lessons.check_requested"
    target_queue: ClassVar[str] = QUEUE_WORKER
    user_id: int
    days: int = 14  # 1 — «за сегодня»; <=0 — сверка с начала семестра


@dataclass(kw_only=True)
class LessonsCheckResult(Event):
    """Результат сверки пар с расписанием (к боту)."""

    event_type: ClassVar[str] = "lessons.check_result"
    target_queue: ClassVar[str] = QUEUE_BOT
    user_id: int
    period: str = ""
    days: int = 14  # 1 — «проверка за сегодня»; <=0 — сверка с начала семестра
    issues: list[dict] = field(default_factory=list)
    error: str = ""


EVENT_TYPES: dict[str, type[Event]] = {
    cls.event_type: cls
    for cls in (
        PollDue,
        CheckRequested,
        MarksChanged,
        AbsencesChanged,
        MailReceived,
        ServiceStatusChanged,
        Auth2FARequired,
        Auth2FACodeResent,
        Auth2FASubmitted,
        Auth2FAResendRequested,
        AuthCompleted,
        Auth2FAFailed,
        AuthFailed,
        ReminderDue,
        LessonsCheckRequested,
        LessonsCheckResult,
    )
}
