"""Парсеры HTML-страниц БАРС (https://bars.mpei.ru/bars_web).

Чистые функции: на входе HTML, на выходе модели (см. integrations.models).
Семантика парсинга оценок совпадает с продакшеном
(master/watchers/api/bars_api.py).
"""

from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup
from loguru import logger

from src.integrations.models import (
    AbsenceRecord,
    DisciplineMarks,
    LessonInfo,
    Mark,
    RewritingMark,
    ScheduleItem,
    StudentBrief,
    StudyGroupInfo,
)

__all__ = [
    "normalize",
    "to_iso_date",
    "parse_day_label",
    "extract_token",
    "extract_2fa_tid",
    "parse_student_brief",
    "parse_student_id",
    "parse_marks",
    "parse_absences",
    "parse_study_group",
    "parse_leaders",
    "current_semester_title",
    "parse_training_journal_id",
    "parse_lessons",
    "parse_schedule",
]

STUDENT_ID_RE = re.compile(r"studentID=([\w]{8}-[\w]{4}-[\w]{4}-[\w]{4}-[\w]{12})")
SG_ID_RE = re.compile(r"sgID=([\w-]{8}-[\w-]{4}-[\w-]{4}-[\w-]{4}-[\w-]{12})")
TJ_ID_RE = re.compile(r"tjID=(\d+)")

# ─── даты/время: структурированные поля (ISO), исходные строки — для показа ───

_DATE_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{2,4})")
_LESSON_HEAD_RE = re.compile(
    r"(?P<date>\d{2}\.\d{2}\.\d{2,4})[^0-9]+(?P<pair>\d+)\s*пара"
    r"\s*\(\s*(?P<t1>\d{1,2}:\d{2})\s*-\s*(?P<t2>\d{1,2}:\d{2})\s*\)"
)
_DAY_LABEL_RE = re.compile(r"(\d{1,2})\s+([а-яё]+)")
_WEEK_RE = re.compile(r"(\d+)\s*нед")
_MONTHS_GEN = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def _hhmm(value: str) -> str:
    """«9:20» → «09:20»."""
    hour, _, minute = value.partition(":")
    try:
        return f"{int(hour):02d}:{minute}"
    except ValueError:
        return value


def to_iso_date(text: str) -> str:
    """Дата из «08.09.26» / «30.08.2024» → ISO «2026-09-08». '' если не найдена."""
    match = _DATE_RE.search(text or "")
    if not match:
        return ""
    day_, month_, year_ = int(match.group(1)), int(match.group(2)), int(match.group(3))
    if year_ < 100:
        year_ += 2000
    try:
        return date(year_, month_, day_).isoformat()
    except ValueError:
        return ""


def _lesson_fields(text: str) -> tuple[str, int | None, str, str]:
    """Из «22.10.24, 2 пара (11:10-12:45), …» → (ISO, 2, '11:10', '12:45')."""
    match = _LESSON_HEAD_RE.search(text or "")
    if not match:
        return "", None, "", ""
    return (
        to_iso_date(match.group("date")),
        int(match.group("pair")),
        _hhmm(match.group("t1")),
        _hhmm(match.group("t2")),
    )


def parse_day_label(
    label: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[str, int | None]:
    """«понедельник, 21 сентября, 4 нед.» → (ISO «2026-09-21», 4).

    Год в подписи не указывается — берётся из диапазона запроса расписания.
    """
    week = None
    if match := _WEEK_RE.search(label or ""):
        week = int(match.group(1))
    match = _DAY_LABEL_RE.search(label or "")
    if not match or match.group(2) not in _MONTHS_GEN:
        return "", week
    day_, month_ = int(match.group(1)), _MONTHS_GEN[match.group(2)]
    years = []
    if date_from:
        years.append(date_from.year)
    if date_to and date_to.year not in years:
        years.append(date_to.year)
    if not years:
        years = [date.today().year]
    candidates = []
    for year_ in years:
        try:
            candidates.append(date(year_, month_, day_))
        except ValueError:
            continue
    if not candidates:
        return "", week
    if date_from and date_to:
        in_range = [c for c in candidates if date_from <= c <= date_to]
        if in_range:
            return in_range[0].isoformat(), week
    anchor = date_from or date.today()
    best = min(candidates, key=lambda c: abs((c - anchor).days))
    return best.isoformat(), week


def normalize(text: str | None) -> str:
    return " ".join((text or "").split())


def extract_token(html: str) -> str | None:
    """__RequestVerificationToken со страницы входа/2FA."""
    soup = BeautifulSoup(html, "html.parser")
    element = soup.find("input", {"name": "__RequestVerificationToken"})
    if element is not None and element.get("value"):
        return element["value"]
    match = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', html)
    return match.group(1) if match else None


def extract_2fa_tid(html: str) -> str | None:
    """Тип 2FA из кнопки a#btnSend.

    onclick выглядит так:
    af2_code_send('btnSend', '/bars_web/Auth/JSON_SendAF2_Code', '2', '4');
    Нужен второй элемент с конца — '2'.
    """
    soup = BeautifulSoup(html, "html.parser")
    button = soup.find("a", id="btnSend")
    if button is None:
        return None
    parts = re.findall(r"'([^']*)'", button.get("onclick") or "")
    if len(parts) >= 3:
        return parts[-2]
    return None


def parse_student_brief(html: str) -> StudentBrief:
    """Студент из tbl__PartialListStudent: studentID + ФИО + группа.

    Группа берётся из 4-го столбца (доступна всем, в отличие от
    StudyGroup/ListStudyGroup — там доступ только у старост/замов).
    """
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="tbl__PartialListStudent")
    if table is None:
        return StudentBrief()
    tbody = table.find("tbody")
    if tbody is None:
        return StudentBrief()
    row = tbody.find("tr")
    link = row.find("a") if row is not None else None
    if link is None:
        return StudentBrief()
    match = STUDENT_ID_RE.search(link.get("href") or "")
    cells = row.find_all("td") if row is not None else []
    group = None
    if len(cells) >= 4:
        group = normalize(cells[3].get_text()) or None
    return StudentBrief(
        student_id=match.group(1) if match else None,
        fio=normalize(link.get_text()) or None,
        group=group,
    )


def parse_student_id(html: str) -> str | None:
    match = STUDENT_ID_RE.search(html)
    return match.group(1) if match else None


def parse_marks(html: str) -> dict[str, DisciplineMarks]:
    """Оценки: {дисциплина: DisciplineMarks}.

    marks[i] — оценка за КМ-(i+1); rewriting — переписывания этой оценки.
    """
    soup = BeautifulSoup(html, "html.parser")
    data: dict[str, DisciplineMarks] = {}

    for discipline in soup.find_all("div", attrs={"class": "my-2"}):
        try:
            name_text = normalize(discipline.get_text())
            name = ",".join(name_text.split(",")[:3])
            if not name:
                continue

            link = discipline.find("a", attrs={"role": "button"})
            href = link.get("href") if link is not None else None
            if not href or not href.startswith("#"):
                continue

            block = soup.find("div", attrs={"id": href[1:]})
            table = block.find("table") if block is not None else None
            tbody = table.find("tbody") if table is not None else None
            if tbody is None:
                continue

            rows = tbody.find_all("tr")
            marks: list[Mark] = []
            for row in rows:
                cells = row.find_all("td")
                if len(cells) != 4:
                    continue
                cell = cells[3]
                # переписывания — span'ы в ячейке; их может быть сколько угодно.
                # Порядок как в БАРС: сначала актуальная оценка (текст ячейки),
                # потом предыдущие оценки («Пересдана: …»), потом блоки
                # «Не учитывается: …» (тоже переписывание, но в балл не входит).
                retaken: list[RewritingMark] = []
                not_counted: list[RewritingMark] = []
                for span in cell.find_all("span"):
                    span_text = normalize(span.get_text())
                    rewrite_parts = span_text.split("(")
                    if len(rewrite_parts) < 2:
                        continue
                    is_not_counted = (
                        "km-not-taken" in (span.get("class") or [])
                        or "Не учитывается" in span_text
                    )
                    entry = RewritingMark(
                        mark=rewrite_parts[0].strip().split()[-1],
                        date=to_iso_date(rewrite_parts[1]),
                        not_counted=is_not_counted,
                    )
                    (not_counted if is_not_counted else retaken).append(entry)
                rewriting = retaken + not_counted
                # текущая оценка — текст ячейки БЕЗ span'ов: одна оценка —
                # текущая; оценок две и более — переписывание и предыдущая
                mark_text = normalize(cell.get_text())
                for span in cell.find_all("span"):
                    mark_text = mark_text.replace(normalize(span.get_text()), " ")
                mark_text = normalize(mark_text)
                if mark_text:
                    parts = mark_text.split("(")
                    mark_value = parts[0].strip()
                    mark_date = to_iso_date(parts[1]) if len(parts) > 1 else ""
                    marks.append(
                        Mark(mark=mark_value, date=mark_date, rewriting=rewriting)
                    )
                else:
                    # ячейка содержит только «Пересдана»/«Не учитывается»
                    marks.append(Mark(rewriting=rewriting))

            mark_pa = ""
            mark_final = ""
            score = ""
            for row in rows:
                cells = row.find_all("td")
                if len(cells) >= 2:
                    text = normalize(cells[0].get_text())
                    value = normalize(cells[-1].get_text())
                    if "Промежуточная" in text:
                        mark_pa = value
                    elif "Итоговая" in text:
                        mark_final = value
                    elif "Балл текущего контроля" in text:
                        score = value

            data[name] = DisciplineMarks(
                name=name,
                marks=marks,
                score=score,
                mark_pa=mark_pa,
                mark_final=mark_final,
            )
        except Exception:
            continue

    return data


def parse_absences(html: str) -> dict[str, AbsenceRecord]:
    """Пропуски: {ключ (data-ls-id): AbsenceRecord}."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="tbl__PartialListStudent__Lesson__Student")
    data: dict[str, AbsenceRecord] = {}
    if table is None:
        return data
    tbody = table.find("tbody")
    if tbody is None:
        return data

    for row in tbody.find_all("tr", attrs={"data-ls-id": True}):
        link = row.find("a")
        lesson = (
            normalize(link.get_text())
            if link is not None
            else normalize(row.get_text())
        )
        items = row.find_all("span", class_="ls-item")
        date_iso, pair_no, time_start, time_end = _lesson_fields(lesson)
        data[row["data-ls-id"]] = AbsenceRecord(
            key=row["data-ls-id"],
            lesson=lesson,
            created=normalize(items[0].get_text()) if len(items) > 0 else "",
            changed=normalize(items[1].get_text()) if len(items) > 1 else "",
            date=date_iso,
            pair=pair_no,
            time_start=time_start,
            time_end=time_end,
        )
    return data


def parse_study_group(html: str) -> StudyGroupInfo:
    """Учебная группа: sg_id + название (первая строка списка)."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="tbl__PartialListStudyGroup")
    if table is None:
        logger.warning("parse_study_group: таблица tbl__PartialListStudyGroup не найдена")
        return StudyGroupInfo()
    tbody = table.find("tbody")
    row = tbody.find("tr") if tbody is not None else None
    link = row.find("a") if row is not None else None
    if link is None:
        logger.warning("parse_study_group: нет ссылки в первой строке таблицы")
        return StudyGroupInfo()
    match = SG_ID_RE.search(link.get("href") or "")
    result = StudyGroupInfo(
        sg_id=match.group(1) if match else None,
        name=normalize(link.get_text()) or None,
    )
    if not result.sg_id:
        logger.warning("parse_study_group: sg_id не найден в href={}", link.get("href"))
    if not result.name:
        logger.warning("parse_study_group: название группы пустое")
    return result


def parse_leaders(html: str) -> list[str]:
    """Ответственные за журнал: логины вида public\\AkimovNA → ['AkimovNA']."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="tbl__PartialListStudyGroup_Leaders")
    if table is None:
        return []
    tbody = table.find("tbody")
    if tbody is None:
        return []
    logins = []
    for row in tbody.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            continue
        raw = normalize(cells[0].get_text())
        if raw:
            logins.append(raw.split("\\")[-1])
    return logins


def current_semester_title(today: date | None = None) -> str:
    """Название текущего семестра, как оно в БАРС: '2026/2027, Осенний семестр'."""
    today = today or date.today()
    if today.month >= 9:
        year1, term = today.year, "Осенний"
    else:
        year1, term = today.year - 1, "Весенний"
    return f"{year1}/{year1 + 1}, {term} семестр"


def parse_training_journal_id(html: str, semester_title: str) -> int | None:
    """tjID учебного журнала нужного семестра (не всегда первая строка)."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="tbl__PartialListStudyGroup__TrainingJournals")
    if table is None:
        return None
    tbody = table.find("tbody")
    if tbody is None:
        return None
    for row in tbody.find_all("tr"):
        link = row.find("a", href=re.compile(r"EditTrainingJournal\?tjID="))
        if link is None:
            continue
        if normalize(link.get_text()) == semester_title:
            match = TJ_ID_RE.search(link.get("href") or "")
            if match:
                return int(match.group(1))
    return None


def parse_lessons(html: str) -> list[LessonInfo]:
    """Занятия журнала: [LessonInfo]."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", id="tbl__PartialListTrainingJournal_Lessons")
    if table is None:
        return []
    tbody = table.find("tbody")
    if tbody is None:
        return []

    lessons = []
    for row in tbody.find_all("tr", attrs={"data-les-id": True}):
        label = row.find("label")
        badge = row.find("span", class_="badge")
        absentees = [
            normalize(span.get_text()).rstrip(";")
            for span in row.find_all("span", class_="ls-item")
        ]
        # отметки присутствия: «Присутствовало 14 чел.» / «нет пропусков»
        attended = "; ".join(
            normalize(span.get_text()).rstrip(",")
            for span in row.find_all("span", class_="ls-header")
        )
        lesson = (
            normalize(label.get_text()) if label else normalize(row.get_text())
        )
        date_iso, pair_no, time_start, time_end = _lesson_fields(lesson)
        lessons.append(
            LessonInfo(
                les_id=row["data-les-id"],
                lesson=lesson,
                status=normalize(badge.get_text()) if badge else "",
                absentees=[name for name in absentees if name],
                attended=attended,
                date=date_iso,
                pair=pair_no,
                time_start=time_start,
                time_end=time_end,
            )
        )
    return lessons


def parse_schedule(
    html: str,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[ScheduleItem]:
    """Расписание (список): [ScheduleItem].

    date_from/date_to — диапазон запроса: из него берётся год для подписи дня.
    """
    soup = BeautifulSoup(html, "html.parser")
    lessons: list[ScheduleItem] = []
    current_day = ""
    current_date = ""
    current_week = None
    for row in soup.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) == 1:  # заголовок дня: 'понедельник, 21 сентября, 4 нед.'
            current_day = normalize(cells[0].get_text())
            current_date, week = parse_day_label(
                current_day, date_from, date_to
            )
            if week is not None:  # «4 нед.» есть не у каждого дня — наследуем
                current_week = week
            continue
        if len(cells) != 2:
            continue
        first_lines = [normalize(line) for line in cells[0].stripped_strings]
        time_raw = first_lines[0] if first_lines else ""
        pair_label = first_lines[1] if len(first_lines) > 1 else ""
        time_start = time_end = ""
        if match := re.search(r"(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})", time_raw):
            time_start, time_end = _hhmm(match.group(1)), _hhmm(match.group(2))
        pair_no = None
        if match := re.search(r"(\d+)", pair_label):
            pair_no = int(match.group(1))
        # аудитория обычно в первой ячейке (ссылка aud_oid), но бывает и в блоке
        room_fallback = ""
        room_link = cells[0].find("a")
        if room_link is not None:
            room_fallback = normalize(room_link.get_text())
        if not room_fallback and len(first_lines) > 2:
            room_fallback = first_lines[2]
        # в одной паре может быть НЕСКОЛЬКО дисциплин (по выбору, разные
        # группы/подгруппы) — блоки разделяются <hr>; каждая — отдельное занятие
        for subject, kind, room, teacher, groups in _schedule_blocks(cells[1]):
            lessons.append(
                ScheduleItem(
                    date=current_date,
                    week=current_week,
                    time_start=time_start,
                    time_end=time_end,
                    pair=pair_no,
                    day=current_day,
                    time=time_raw,
                    pair_label=pair_label,
                    room=room or room_fallback,
                    subject=subject,
                    kind=kind,
                    groups=groups,
                    teacher=teacher,
                )
            )
    return lessons


# тип занятия в скобках предмета: «Дискретная математика (Лекция)»
_SUBJECT_KIND_RE = re.compile(
    r"^(?P<subject>.+?)\s*\((?P<kind>[^()]*(?:лекци|лабораторн|практи|семинар|"
    r"консультац|экзамен|зач[ёе]т|курсов)[^()]*)\)\s*$",
    re.IGNORECASE,
)
# тип занятия отдельной строкой (случай td-today): «Лекция», «Лабораторная работа»
_KIND_LINE_RE = re.compile(
    r"^(?:лекци|лабораторн|практи|семинар|консультац|экзамен|зач[ёе]т|курсов)",
    re.IGNORECASE,
)


def _schedule_blocks(cell) -> list[tuple[str, str, str, str, str]]:
    """Разобрать ячейку расписания на дисциплины: (предмет, тип, аудитория, преподаватель, группы).

    Два вида разметки:
      - обычный: <strong>Дискретная математика (Лекция)</strong>, ссылки на
        преподавателя; аудитория — в первой ячейке;
      - td-today (две дисциплины в одну пару): блоки через <hr>, в каждом
        <strong>Предмет</strong>, тип строкой, аудитория и преподаватель ссылками.
    """
    blocks: list[list] = []
    current: list = []
    for child in cell.children:
        if getattr(child, "name", None) == "hr":
            blocks.append(current)
            current = []
        else:
            current.append(child)
    blocks.append(current)

    parsed: list[tuple[str, str, str, str, str]] = []
    for nodes in blocks:
        subject = kind = room = teacher = ""
        plain: list[str] = []
        for node in nodes:
            name = getattr(node, "name", None)
            if name == "strong":
                subject = subject or normalize(node.get_text())
            elif name == "a":
                href = node.get("href", "") or ""
                text = normalize(node.get_text())
                if not text:
                    continue
                if "lec_oid" in href:
                    teacher = text
                elif "aud_oid" in href:
                    room = text
                elif not teacher:
                    teacher = text  # старый вид: единственная ссылка — преподаватель
            elif name is None:  # текстовая строка: тип занятия / группы
                text = normalize(str(node))
                if text:
                    plain.append(text)
        # тип может быть в скобках предмета либо отдельной строкой
        if match := _SUBJECT_KIND_RE.match(subject or ""):
            subject = normalize(match.group("subject"))
            kind = kind or normalize(match.group("kind"))
        group_lines: list[str] = []
        for text in plain:
            if not kind and _KIND_LINE_RE.match(text):
                kind = text
            else:
                group_lines.append(text)
        if not subject and not kind:
            continue  # пустой блок
        parsed.append(
            (subject, kind, room, teacher, ", ".join(group_lines))
        )
    return parsed
