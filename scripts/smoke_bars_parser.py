"""Smoke-проверка парсеров БАРС на синтетических HTML (по реальным шаблонам).

Запуск: uv run python scripts/smoke_bars_parser.py
"""

import os
import sys
from datetime import date

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("SECRET_KEY", "smoke")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:smoke")
os.environ.setdefault("ADMINS", "[1]")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", "postgres")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "barswatcher")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def check(condition: bool, label: str) -> None:
    status = "OK " if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        raise SystemExit(1)


from src.integrations.bars.parser import (
    access_forbidden,
    extract_2fa_tid,
    extract_token,
    parse_absences,
    parse_day_label,
    parse_leaders,
    parse_marks,
    parse_student_brief,
    parse_study_group,
)

# ── токен и tid 2FA ───────────────────────────────────────────────────────────
LOGIN_PAGE = """
<html><body>
<form action="/bars_web/" method="post">
<input name="__RequestVerificationToken" type="hidden" value="TOK-123" /><input ...>
</form>
</body></html>
"""
check(extract_token(LOGIN_PAGE) == "TOK-123", "токен __RequestVerificationToken")

TWOFA_PAGE = """
<div><a class="btn" id="btnSend"
   onclick="af2_code_send('btnSend', '/bars_web/Auth/JSON_SendAF2_Code', '2', '4');">Send</a></div>
"""
check(extract_2fa_tid(TWOFA_PAGE) == "2", "tid 2FA — второй элемент с конца")

# ── studentID / группа / ответственные ────────────────────────────────────────
STUDENT_PAGE = """
<table id="tbl__PartialListStudent"><tbody><tr>
<td><a href="/bars_web/ST/Student/Main?studentID=1c50ca9c-6c95-4f4d-858e-6fbaa0cfa7fb">Иванов Иван Иванович</a></td>
</tr></tbody></table>
"""
brief = parse_student_brief(STUDENT_PAGE)
check(
    brief.student_id == "1c50ca9c-6c95-4f4d-858e-6fbaa0cfa7fb",
    "studentID из списка студентов",
)
check(brief.fio == "Иванов Иван Иванович", "ФИО из ссылки")

GROUP_PAGE = """
<table id="tbl__PartialListStudyGroup"><tbody><tr>
<td><a href="/bars_web/SG/StudyGroup/EditStudyGroup?sgID=ce991316-11df-ed11-80d9-005056be401c">А-16-23</a></td>
</tr></tbody></table>
"""
group = parse_study_group(GROUP_PAGE)
check(group.sg_id == "ce991316-11df-ed11-80d9-005056be401c", "sgID группы")
check(group.name == "А-16-23", "название группы")

LEADERS_PAGE = """
<table id="tbl__PartialListStudyGroup_Leaders"><tbody>
<tr><td>public\\AkimovNA</td><td>Староста</td></tr>
<tr><td>public\\PetrovPP</td><td>Зам</td></tr>
</tbody></table>
"""
check(
    parse_leaders(LEADERS_PAGE) == ["AkimovNA", "PetrovPP"],
    "ответственные за журнал (логины без public\\)",
)

# ── право «ответственный за журнал» (EditStudyGroup доступ / запрещён) ────────
ACCESS_DENIED_PAGE = "<html><body><h1>Доступ запрещён</h1></body></html>"
check(access_forbidden(ACCESS_DENIED_PAGE), "страница «Доступ запрещён» распознана")
check(
    access_forbidden("<html><body>Access Denied</body></html>"),
    "страница Access Denied (en) распознана",
)
check(
    not access_forbidden(GROUP_PAGE),
    "обычная страница группы не считается «доступ запрещён»",
)

# ── оценки ────────────────────────────────────────────────────────────────────
MARKS_PAGE = """
<div class="my-2">
    <div data-bs-toggle="collapse" data-bs-target="#s_ss_0" style="cursor: pointer; display: inline">
        Информатика, Фомин Д.А., зачёт с оценкой, 5ЗЕ
        <span class="badge bg-light" title="...">сдать до 23.12.2026</span>
    </div>
    <div style="display: inline"><a href="#s_ss_0" role="button">Развернуть</a></div>
</div>
<div id="s_ss_0" class="collapse">
    <table><tbody>
        <tr><td colspan="4"><em>Текущий контроль:</em></td></tr>
        <tr class="tr-odd">
            <td>1. Проектирование реляционных баз данных</td><td>20</td>
            <td>4 (21.09.26-27.09.26)</td><td>5  (08.09.26)</td>
        </tr>
        <tr id="divMERes0" class="collapse tr-odd"><td colspan="4">критерии</td></tr>
        <tr>
            <td>2. Разработка запросов на языке SQL</td><td>40</td>
            <td>8 (19.10.26-25.10.26)</td><td></td>
        </tr>
        <tr><td colspan="3">Балл текущего контроля: </td><td>5,00</td></tr>
        <tr class="tr-odd"><td colspan="2">Контрольная неделя №1</td><td>4</td><td></td></tr>
        <tr><td colspan="3"><em>Промежуточная аттестация (зачёт с оценкой):</em></td>
            <td><a class="btn btn-link p-0" data-ss-exam-auto-id="2273413"></a></td></tr>
        <tr><td colspan="3"><strong>Итоговая оценка:</strong></td>
            <td style="font-weight: bold">5</td></tr>
    </tbody></table>
</div>
"""
marks = parse_marks(MARKS_PAGE)
check(len(marks) == 1, "одна дисциплина")
name = "Информатика, Фомин Д.А., зачёт с оценкой"
check(name in marks, f"название дисциплины: {list(marks)}")
data = marks[name]
check(
    data.marks[0].mark == "5" and data.marks[0].date == "2026-09-08",
    "КМ-1: оценка и дата (ISO)",
)
check(not data.marks[0].rewriting, "КМ-1: без переписываний")
check(not data.marks[1].mark and not data.marks[1].date, "КМ-2: пусто")
check(data.mark_final == "5", "итоговая оценка")

# переписывание: span'ы «Пересдана»/«Не учитывается» внутри ячейки оценки
REWRITE_PAGE = MARKS_PAGE.replace(
    "<td>5  (08.09.26)</td>",
    '<td>5  (08.09.26)<br />'
    '<span class="km-retaken"> Пересдана: 4 (01.09.26) </span><br />'
    '<span class="km-not-taken"> Не учитывается: 3 (15.09.26) </span></td>',
)
rewrite_data = parse_marks(REWRITE_PAGE)[name]
check(
    [(m.mark, m.date, m.not_counted) for m in rewrite_data.marks[0].rewriting]
    == [("4", "2026-09-01", False), ("3", "2026-09-15", True)],
    "переписывания: предыдущие (Пересдана), затем Не учитывается",
)

# ячейка только со span'ами: текущей оценки нет, история переписываний есть
ONLY_SPANS_PAGE = MARKS_PAGE.replace(
    "<td>5  (08.09.26)</td>",
    '<td><span class="km-not-taken"> Не учитывается: 3 (15.09.26) </span></td>',
)
only_data = parse_marks(ONLY_SPANS_PAGE)[name]
check(
    only_data.marks[0].mark == ""
    and [(m.mark, m.date, m.not_counted) for m in only_data.marks[0].rewriting]
    == [("3", "2026-09-15", True)],
    "ячейка только со span'ами: оценки нет, переписывание учтено",
)
# ── пропуски ──────────────────────────────────────────────────────────────────
ABSENCES_PAGE = """
<table id="tbl__PartialListStudent__Lesson__Student"><tbody>
<tr data-ls-id="27414363">
    <td>
        <label style="font-weight: normal"><input type="checkbox" class="form-check-input" />
            <a href="/bars_web/SG/AttendanceSheet/EditAttendanceSheet?asID=91448#Lessons">
                13.11.25, 3 пара (13:45-15:20), лекция (Бартеньев О.В.), Компьютерная графика (зачёт с оценкой), А-16-23
            </a>
        </label>
        <span class="badge bg-danger" style="font-size: 12px;">пропуск</span>
        <br />
        <span class="ls-header">Запись занятия создана:</span>
        <span class="ls-item"> 17.12.2025 13:25:37 Петрова М.А.</span>
        <br />
        <span class="ls-header">Последнее изменение:</span>
        <span class="ls-item"> 22.12.2025 20:46:33 Бартеньев О.В.</span>
    </td>
    <td></td>
</tr>
</tbody></table>
"""
absences = parse_absences(ABSENCES_PAGE)
check(list(absences) == ["27414363"], "ключ пропуска = data-ls-id")
row = absences["27414363"]
check(
    row.lesson.startswith("13.11.25, 3 пара (13:45-15:20)"),
    "текст занятия пропуска",
)
check(row.created.endswith("Петрова М.А."), "кто создал запись")
check(row.changed.endswith("Бартеньев О.В."), "кто последний менял")
check(
    row.date == "2025-11-13"
    and row.pair == 3
    and row.time_start == "13:45"
    and row.time_end == "15:20",
    "структурированные поля пропуска (дата ISO / пара / время)",
)

# подпись дня расписания: «понедельник, 21 сентября, 4 нед.»
day_iso, week = parse_day_label(
    "понедельник, 21 сентября, 4 нед.",
    date(2026, 9, 21),
    date(2026, 9, 27),
)
check(
    day_iso == "2026-09-21" and week == 4,
    "день расписания → дата ISO + номер недели",
)

print("\nПарсеры БАРС: smoke пройден ✅")
