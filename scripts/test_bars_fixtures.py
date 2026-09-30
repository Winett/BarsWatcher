"""Проверка парсеров БАРС на реальных HTML-ответах (файлы *.html в simdata/bars).

Запуск: uv run python scripts/test_bars_fixtures.py
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

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

import json  # noqa: E402

from src.integrations.bars import parser  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "simdata" / "bars"

FILES = {
    "login": "bars_web.html",
    "2fa": "bars_web.after_login_and_2fa_required.html",
    "students": "bars_web.ST.Student._PartialListStudent.html",
    "marks": "bars_web.ST_Study.Student_SemesterSheet._PartialListStudent_SemesterSheet__Mark.html",
    "absences": "bars_web.ST_Study.Lesson__Student._PartialListStudent__Lesson__Student.html",
    "groups": "bars_web.SG.StudyGroup._PartialListStudyGroup.html",
    "leaders": "bars_web.SG.StudyGroupLeader._PartialListStudyGroup_Leaders.html",
    "journals": "bars_web.SG.TrainingJournal._PartialListStudyGroup__TrainingJournals.html",
    "lessons": "bars_web.SG.Lesson._PartialListTrainingJournal_Lessons.html",
}


def load(name: str) -> str:
    path = FIXTURES / FILES[name]
    if not path.exists():
        print(f"[FAIL] нет файла {path.name}")
        raise SystemExit(1)
    return path.read_text(encoding="utf-8", errors="replace")


def dump(label: str, data) -> None:
    if is_dataclass(data) and not isinstance(data, type):
        data = asdict(data)
    elif isinstance(data, dict):
        data = {
            key: asdict(value) if is_dataclass(value) else value
            for key, value in data.items()
        }
    elif isinstance(data, list):
        data = [asdict(value) if is_dataclass(value) else value for value in data]
    print(f"\n=== {label} ===")
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str)[:3000])


def main() -> None:
    # 1. страница входа: токен
    token = parser.extract_token(load("login"))
    print(f"[{'OK ' if token else 'FAIL'}] токен со страницы входа: {(token or '')[:20]}...")

    # 2. страница 2FA: токен + tid
    twofa_html = load("2fa")
    token2 = parser.extract_token(twofa_html)
    tid = parser.extract_2fa_tid(twofa_html)
    print(f"[{'OK ' if token2 else 'FAIL'}] токен со страницы 2FA")
    print(f"[{'OK ' if tid else 'FAIL'}] tid 2FA (a#btnSend): {tid}")

    # 2b. текст ошибки входа не должен находиться на «нормальных» страницах
    from src.integrations.bars.auth import _extract_error_text

    no_err = (
        _extract_error_text(load("login")) is None
        and _extract_error_text(twofa_html) is None
    )
    print(
        f"[{'OK ' if no_err else 'FAIL'}] ложных ошибок входа на страницах "
        "входа/2FA нет"
    )
    has_err = _extract_error_text(
        "<div class=\"validation-summary-errors\"><ul>"
        "<li>Неверно указан логин или пароль</li></ul></div>"
    )
    print(
        f"[{'OK ' if has_err else 'FAIL'}] явная ошибка входа распознаётся: {has_err!r}"
    )

    # 3. студент
    brief = parser.parse_student_brief(load("students"))
    dump("студент", brief)

    # 4. оценки
    marks = parser.parse_marks(load("marks"))
    print(f"\n=== оценки: {len(marks)} дисциплин ===")
    for name, data in marks.items():
        filled = sum(1 for m in data.marks if m.mark)
        rewrites = sum(len(m.rewriting) for m in data.marks)
        print(
            f"  - {name}: КМ={len(data.marks)}, с оценкой={filled}, "
            f"переписываний={rewrites}, ПА={data.mark_pa!r}, итог={data.mark_final!r}"
        )

    # 4b. переписывания (rewrite_mark.html): «Пересдана»/«Не учитывается»
    rewrite_path = FIXTURES / "rewrite_mark.html"
    if rewrite_path.exists():
        rmarks = parser.parse_marks(
            rewrite_path.read_text(encoding="utf-8", errors="replace")
        )
        print(
            f"\n=== переписывания (rewrite_mark.html): {len(rmarks)} дисциплин ==="
        )
        total_rw = 0
        for name, data in rmarks.items():
            for i, mark in enumerate(data.marks, 1):
                if mark.rewriting:
                    total_rw += len(mark.rewriting)
                    print(
                        f"  - {name[:45]} КМ-{i}: текущая={mark.mark!r} ({mark.date}), "
                        f"переписывания={[(r.mark, r.date, r.not_counted) for r in mark.rewriting]}"
                    )
        la = next(
            (d for n, d in rmarks.items() if n.startswith("Линейная алгебра")), None
        )
        ok = (
            la is not None
            and la.marks[0].mark == "2"
            and la.marks[0].date == "2024-04-01"
            and [(r.mark, r.date, r.not_counted) for r in la.marks[0].rewriting]
            == [("2", "2024-03-14", False), ("3", "2024-06-03", True)]
            and [(r.mark, r.date, r.not_counted) for r in la.marks[1].rewriting]
            == [("2", "2024-04-11", False)]
            and la.marks[2].mark == "3"
            and [(r.mark, r.date, r.not_counted) for r in la.marks[2].rewriting]
            == [("2", "2024-05-10", False)]
            and la.mark_final == "3"
        )
        print(
            f"[{'OK ' if ok else 'FAIL'}] переписывания разобраны по образцу "
            "(Линейная алгебра: КМ-1..3)"
        )
        print(f"всего записей переписываний: {total_rw}")

        # определение переписывания в diff: оценка та же + появился span
        from src.integrations.models import marks_to_snapshot
        from src.services.diff import diff_bars_marks

        if la is not None:
            snapshot = marks_to_snapshot({la.name: la})
            before = {
                la.name: {
                    "marks": [
                        {
                            "mark": "2",
                            "date": "2024-04-01",
                            "rewriting": [
                                {
                                    "mark": "2",
                                    "date": "2024-03-14",
                                    "not_counted": False,
                                }
                            ],
                        },
                        {"mark": "3", "date": "2024-05-02", "rewriting": []},
                    ],
                    "score": la.score,
                    "mark_pa": la.mark_pa,
                    "mark_final": la.mark_final,
                }
            }
            changes = diff_bars_marks(before, snapshot)
            kinds = sorted({c["kind"] for c in changes})
            ok_rw = any(c["kind"] == "rewriting" and c["km"] == 1 for c in changes)
            # новый блок «Не учитывается» — тоже переписывание, с флагом
            ok_nc = any(
                c["kind"] == "rewriting" and c.get("not_counted") for c in changes
            )
            print(
                f"[{'OK ' if ok_rw and ok_nc else 'FAIL'}] diff распознаёт переписывание "
                f"(kinds={kinds}, not_counted={'учтён' if ok_nc else 'НЕТ'})"
            )
    else:
        print("[FAIL] нет rewrite_mark.html")

    # 4c. расписание RUZ (bars_web.Open.RUZ._PartialTimetable.html)
    sched_path = FIXTURES / "bars_web.Open.RUZ._PartialTimetable.html"
    if sched_path.exists():
        from datetime import date as _date

        sched = parser.parse_schedule(
            sched_path.read_text(encoding="utf-8", errors="replace"),
            _date(2026, 9, 21),
            _date(2026, 9, 27),
        )
        print(f"\n=== расписание RUZ: {len(sched)} занятий ===")
        for item in sched[:5]:
            print(
                f"  - [{item.date}, {item.week} нед.] {item.time} {item.pair_label} | "
                f"{item.room} | {item.subject} | {item.groups} | {item.teacher}"
            )
        first = sched[0] if sched else None
        ok = (
            first is not None
            and first.date == "2026-09-21"
            and first.week == 4
            and first.time_start == "09:20"
            and first.time_end == "10:55"
            and first.pair == 1
            and first.subject == "Дискретная математика (Лекция)"
            and first.groups == "Б-11,12-21"
            and first.teacher == "доц. Заславский А.А."
            and all(item.week == 4 for item in sched)  # неделя наследуется днями
        )
        print(
            f"[{'OK ' if ok else 'FAIL'}] расписание разобрано "
            "(дата ISO / неделя / пара / время / аудитория)"
        )
    else:
        print("[FAIL] нет bars_web.Open.RUZ._PartialTimetable.html")

    # 5. пропуски
    absences = parser.parse_absences(load("absences"))
    print(f"\n=== пропуски: {len(absences)} записей ===")
    for key, row in list(absences.items())[:5]:
        print(f"  - [{key}] {row.lesson[:80]}")

    # 6. группа
    dump("группа", parser.parse_study_group(load("groups")))

    # 7. ответственные
    leaders = parser.parse_leaders(load("leaders"))
    dump("ответственные за журнал", leaders)

    # 8. журналы (tjID текущего семестра)
    journals_html = load("journals")
    title = parser.current_semester_title()
    tj_id = parser.parse_training_journal_id(journals_html, title)
    print(f"\n[{'OK ' if tj_id else 'FAIL'}] tjID семестра «{title}»: {tj_id}")

    # 9. занятия журнала
    lessons = parser.parse_lessons(load("lessons"))
    print(f"\n=== занятия журнала: {len(lessons)} ===")
    for lesson in lessons[:5]:
        print(
            f"  - [{lesson.les_id}] {lesson.lesson[:70]} | статус={lesson.status!r} "
            f"| отсутствующих={len(lesson.absentees)}"
        )

    print("\nПроверка фикстур завершена ✅")


if __name__ == "__main__":
    main()
