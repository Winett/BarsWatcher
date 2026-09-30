"""Сверка занятий журнала с расписанием и проверка «проставлены ли пары».

Правила (для напоминаний ответственным за журнал):
- пара есть в расписании, а занятия в журнале нет → пара не проставлена;
- статус «требуется корректировка» → пара не в порядке;
- статус «на рассмотрении» и ни один студент не отмечен → пара не в порядке;
- «согласовано» или «на рассмотрении» с отметками студентов → всё ок.

Занятие в журнале сопоставляется с парой расписания по (дата, номер пары);
две дисциплины в одно время (разные группы/подгруппы) парой считаются одной.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date

__all__ = [
    "LessonIssue",
    "find_lesson_issues",
    "describe_issue",
    "describe_missing",
    "semester_start",
    "split_lesson_raw",
]

# raw занятия журнала:
# «22.12.26, 5 пара (17:20-18:50), лабораторная работа (Соколов А.Е.), Методы ... (экзамен)»
_LESSON_RE = re.compile(
    r"^(?P<date>\d{2}\.\d{2}\.\d{2}),\s*"
    r"(?P<pair>\d+)\s*пара\s*\((?P<time>[^)]*)\),\s*"
    r"(?P<kind>[^()]+?)\s*\((?P<teacher>[^)]*)\),\s*"
    r"(?P<subject>.*)$"
)


def split_lesson_raw(lesson: str) -> dict:
    """Разобрать raw-строку занятия на поля (для показа в таком виде, как просили)."""
    match = _LESSON_RE.match(lesson or "")
    if not match:
        return {
            "date_label": "",
            "pair": None,
            "time": "",
            "kind": "",
            "teacher": "",
            "subject": lesson or "",
        }
    return {
        "date_label": match.group("date"),
        "pair": int(match.group("pair")),
        "time": match.group("time"),
        "kind": match.group("kind").strip(),
        "teacher": match.group("teacher").strip(),
        "subject": match.group("subject").strip(),
    }


@dataclass
class LessonIssue:
    """Проблема с парой: что именно не так."""

    kind: str  # "missing" (нет в журнале) | "status" | "not_marked"
    date: str  # ISO «2026-09-25»
    date_label: str  # «25.09.26»
    pair: int | None
    time: str  # «9:20-10:55»
    subject: str  # «Механическая физика»
    lesson_kind: str  # «лабораторная работа»
    teacher: str  # «Соколов А.Е.»
    status: str  # «требуется корректировка»
    reason: str  # «студенты не отмечены»

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LessonIssue:
        return cls(**{key: data.get(key, "") for key in cls.__dataclass_fields__})


def describe_issue(issue: LessonIssue) -> str:
    """Строка для напоминания (детали прямо из журнала)."""
    time = f" ({issue.time})" if issue.time else ""
    pair = f"{issue.pair} пара" if issue.pair else "пара"
    lesson_part = f", {issue.lesson_kind} ({issue.teacher})" if issue.lesson_kind else ""
    status_part = f" стоит статус «{issue.status}»" if issue.status else ""
    return (
        f"{issue.date_label} {issue.subject} {pair}{time}{lesson_part}"
        f"{status_part}, {issue.reason}"
    )


def describe_missing(issue: LessonIssue) -> str:
    """Строка для сверки: «За 20.09.26 пропущена пара 2 по Дифференциальные уравнения (Преподаватель)»."""
    pair = f"{issue.pair}" if issue.pair else "?"
    subject = issue.subject or "предмет не определён"
    teacher = f" ({issue.teacher})" if issue.teacher else ""
    return f"За {issue.date_label} пропущена пара {pair} по {subject}{teacher}"


def _schedule_parts(subject: str) -> tuple[str, str]:
    """Расписание: «Дисциплина (Практическое занятие)» → (дисциплина, тип)."""
    match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", subject or "")
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return (subject or "").strip(), ""


def _subject_key(name: str) -> str:
    """Нормализованное название предмета для сопоставления журнала и расписания.

    Убирает пометки в скобках: «(по выбору)», «(Лекция)», «(Практическое занятие)»…
    """
    text = (name or "").lower().replace("ё", "е")
    text = re.sub(
        r"\([^)]*(?:по выбору|лекци|лабораторн|практи|семинар|консультац|"
        r"экзамен|зач[ее]т|курсов)[^)]*\)",
        " ",
        text,
    )
    return re.sub(r"\s+", " ", text).strip(" .,-")


def _subject_matches(wanted_key: str, raw_subject: str) -> bool:
    """Совпадает ли предмет занятия журнала с предметом пары расписания."""
    other_key = _subject_key(raw_subject)
    if not wanted_key or not other_key:
        return False
    if wanted_key == other_key:
        return True
    # сокращения в названии — по общему началу (от 5 символов)
    shorter, longer = sorted((wanted_key, other_key), key=len)
    return len(shorter) >= 5 and longer.startswith(shorter)


def semester_start(today: date) -> date:
    """Начало текущего семестра (МПЭИ: осень — с 1 сентября, весна — с 1 февраля)."""
    if today.month >= 9:
        return date(today.year, 9, 1)
    if today.month == 1:  # январь — конец осеннего семестра
        return date(today.year - 1, 9, 1)
    return date(today.year, 2, 1)


def find_lesson_issues(
    schedule: list, lessons: list
) -> list[LessonIssue]:
    """Сверка: какие пары не проставлены или проставлены с проблемой.

    - каждое занятие журнала проверяется на статус и отметки студентов;
    - каждая пара расписания без занятия в журнале (по предмету) —
      «пара не проставлена»; в одну пару бывает несколько дисциплин —
      каждая проверяется отдельно.
    """
    issues: list[LessonIssue] = []

    # 1) занятия журнала: статус и отметки студентов
    for lesson in lessons:
        status = (lesson.status or "").strip()
        status_lower = status.lower()
        if "согласовано" in status_lower:
            continue  # всё ок
        if "рассмотрении" in status_lower and lesson.has_marks:
            continue  # на рассмотрении, но студенты отмечены — ок

        parts = split_lesson_raw(lesson.lesson)
        if not lesson.has_marks:
            reason = "студенты не отмечены"
        elif "корректировк" in status_lower:
            reason = "требуется корректировка"
        else:
            reason = "требует внимания"
        issues.append(
            LessonIssue(
                kind="not_marked" if not lesson.has_marks else "status",
                date=lesson.date or "",
                date_label=parts["date_label"] or _date_label(lesson.date),
                pair=lesson.pair,
                time=parts["time"] or _time_range(lesson.time_start, lesson.time_end),
                subject=parts["subject"],
                lesson_kind=parts["kind"],
                teacher=parts["teacher"],
                status=status,
                reason=reason,
            )
        )

    # 2) пары расписания, которых нет в журнале — не проставлены.
    # В одну пару может быть несколько дисциплин (по выбору, разные группы):
    # каждая своя — сверяем по предмету, недостающие показываем пользователю.
    for item in schedule or []:
        subject, lesson_kind = _schedule_parts(item.subject)
        wanted = _subject_key(subject)
        if any(
            lesson.date == item.date
            and lesson.pair == item.pair
            and _subject_matches(wanted, split_lesson_raw(lesson.lesson)["subject"])
            for lesson in lessons
        ):
            continue
        issues.append(
            LessonIssue(
                kind="missing",
                date=item.date or "",
                date_label=_date_label(item.date),
                pair=item.pair,
                time=_time_range(item.time_start, item.time_end),
                subject=subject,
                lesson_kind=item.kind or lesson_kind,
                teacher=item.teacher or "",
                status="",
                reason="пара не проставлена",
            )
        )

    issues.sort(key=lambda issue: (issue.date, issue.pair or 0))
    return issues


def _date_label(date_iso: str) -> str:
    """«2026-09-25» → «25.09.26»."""
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_iso or "")
    if not match:
        return date_iso or ""
    return f"{match.group(3)}.{match.group(2)}.{match.group(1)[2:]}"


def _time_range(start: str, end: str) -> str:
    if start and end:
        return f"{start}-{end}"
    return start or end or ""
