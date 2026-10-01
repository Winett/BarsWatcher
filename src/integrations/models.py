"""Модели данных интеграций — вместо «сырых» dict.

Клиенты возвращают эти dataclass'ы, чтобы были известны все атрибуты.
Снимки в Redis и payload событий — обычные dict (dataclasses.asdict),
diff работает над dict-снимками (см. services.diff).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

__all__ = [
    "RewritingMark",
    "Mark",
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
    "marks_to_snapshot",
    "absences_to_snapshot",
]


# ────────────────────────────── БАРС: оценки ──────────────────────────────


@dataclass
class RewritingMark:
    """Оценка за переписывание / предыдущая оценка (может быть сколько угодно).

    not_counted=True — блок «Не учитывается»: тоже оценка за переписывание,
    но в балл не входит. date — ISO («2026-09-08»), пусто если нет.
    """

    mark: str
    date: str = ""
    not_counted: bool = False


@dataclass
class Mark:
    """Оценка за контрольное мероприятие (КМ). mark="" — оценки ещё нет.

    date — ISO («2026-09-08»), пусто если нет.
    """

    mark: str = ""
    date: str = ""
    rewriting: list[RewritingMark] = field(default_factory=list)


@dataclass
class DisciplineMarks:
    """Оценки по одной дисциплине."""

    name: str
    marks: list[Mark] = field(default_factory=list)  # КМ-1..N по порядку
    score: str = ""  # балл текущего контроля
    mark_pa: str = ""  # оценка промежуточной аттестации (ПА)
    mark_final: str = ""  # итоговая оценка


# ────────────────────────────── БАРС: пропуски/профиль ──────────────────────────────


@dataclass
class AbsenceRecord:
    """Запись о пропуске занятия.

    Структурированные поля (date/pair/time_start/time_end) — для вычислений
    (напоминания, статистика, diff); lesson — исходная строка для показа.
    """

    key: str  # id записи в БАРС (data-ls-id)
    lesson: str  # raw: «13.11.25, 3 пара (13:45-15:20), лекция (...), Дисциплина, Группа»
    created: str = ""  # кто и когда создал запись (raw)
    changed: str = ""  # последнее изменение (raw)
    date: str = ""  # ISO дата занятия «2025-11-13»
    pair: int | None = None  # номер пары (3)
    time_start: str = ""  # «13:45»
    time_end: str = ""  # «15:20»


@dataclass
class StudentBrief:
    """Студент из списка (studentID + ФИО + группа).

    Группа берётся из ListStudent (4-й столбец) — доступно всем,
    в отличие от StudyGroup/ListStudyGroup (только старостам/замам).
    """

    student_id: str | None = None
    fio: str | None = None
    group: str | None = None


@dataclass
class StudentProfile:
    """Профиль пользователя для панели/напоминаний."""

    fio: str | None = None
    group: str | None = None
    is_journal_responsible: bool = False  # ответственный за журнал (староста/зам)


@dataclass
class StudyGroupInfo:
    """Учебная группа."""

    sg_id: str | None = None
    name: str | None = None


# ────────────────────────────── БАРС: занятия/расписание ──────────────────────────────


@dataclass
class LessonInfo:
    """Занятие из учебного журнала (для напоминаний о парах).

    Структурированные поля — для вычислений; lesson — исходная строка для показа.
    """

    les_id: str
    lesson: str  # raw: «24.09.26, 5 пара (17:20-18:50), практическое занятие (...), ...»
    status: str = ""  # «согласовано» / «на рассмотрении» / «требуется корректировка»
    absentees: list[str] = field(default_factory=list)
    attended: str = ""  # «Присутствовало 14 чел.» (пусто, если никто не отмечен)
    date: str = ""  # ISO дата занятия «2026-09-24»
    pair: int | None = None  # номер пары (5)
    time_start: str = ""  # «17:20»
    time_end: str = ""  # «18:50»

    @property
    def has_marks(self) -> bool:
        """Студенты отмечены: есть отметки присутствия или пропуски."""
        return bool(self.attended or self.absentees)


@dataclass
class ScheduleItem:
    """Занятие из расписания RUZ (открытый эндпоинт).

    Структурированные поля (date/week/time_start/time_end/pair) — для вычислений
    (напоминания, статистика, diff); day/time/pair_label — исходный текст для показа.
    """

    date: str = ""  # ISO «2026-09-21» (год выводится из диапазона запроса)
    week: int | None = None  # «4 нед.» → 4
    time_start: str = ""  # «17:20»
    time_end: str = ""  # «18:50»
    pair: int | None = None  # «5 пара» → 5
    day: str = ""  # raw: «понедельник, 21 сентября, 4 нед.»
    time: str = ""  # raw: «17:20-18:50»
    pair_label: str = ""  # raw: «5 пара»
    room: str = ""  # «А-103 (Корпус А)»
    subject: str = ""  # «Математическая логика...»
    kind: str = ""  # «Лекция» / «Практическое занятие» / …
    groups: str = ""
    teacher: str = ""


# ────────────────────────────── 2FA ──────────────────────────────


@dataclass
class TwoFASendResult:
    """Результат отправки кода 2FA."""

    success: bool
    destination: str = ""  # «Сообщение отправлено в 'ВКонтакте'»


# ────────────────────────────── ОСЭП ──────────────────────────────


@dataclass
class Attachment:
    filename: str
    content_type: str = ""
    size: int = 0
    attachment_id: str = ""  # id вложения в сервисе (скачивание/кэш)


@dataclass
class OsepFolder:
    """Папка ОСЭП (OWA): имя, id, счётчики."""

    name: str  # «Входящие»
    folder_id: str
    total_count: int = 0
    unread_count: int = 0


@dataclass
class MailMessage:
    """Письмо из ОСЭП."""

    subject: str
    sender_name: str = ""
    sender_email: str = ""
    body: str = ""
    has_attachments: bool = False
    attachments: list[Attachment] = field(default_factory=list)


# ────────────────────────────── снимки (Redis) ──────────────────────────────

# служебный ключ снимка: метка текущего семестра (для распознавания смены)
SEMESTER_KEY = "_semester"


def marks_to_snapshot(
    marks: dict[str, DisciplineMarks], semester: str | None = None
) -> dict:
    """dict-снимок для Redis: {дисциплина: {marks: [...], mark_pa, mark_final}}."""
    snapshot = {name: asdict(values) for name, values in marks.items()}
    if semester is not None:
        snapshot[SEMESTER_KEY] = semester
    return snapshot


def absences_to_snapshot(
    absences: dict[str, AbsenceRecord], semester: str | None = None
) -> dict:
    """dict-снимок для Redis: {ключ: {key, lesson, created, changed}}."""
    snapshot = {key: asdict(values) for key, values in absences.items()}
    if semester is not None:
        snapshot[SEMESTER_KEY] = semester
    return snapshot
