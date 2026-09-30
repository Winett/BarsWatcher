"""Smoke-проверка ядра без БД/Redis: импорты, контракты, слияние настроек, diff.

Запуск: uv run python scripts/smoke_core.py
"""

import os
import sys

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


# 1. импорты всех слоёв ядра
import src.core.config  # noqa: F401
import src.core.logger  # noqa: F401
import src.db.database  # noqa: F401
import src.db.models  # noqa: F401
import src.events  # noqa: F401
import src.integrations  # noqa: F401
import src.repositories  # noqa: F401
import src.services  # noqa: F401
import src.workers.settings  # noqa: F401

print("[OK ] импорты всех слоёв")

# 2. контракты событий: roundtrip + маршрутизация очередей
from src.events import (
    EVENT_TYPES,
    QUEUE_BOT,
    QUEUE_WORKER,
    Auth2FASubmitted,
    LessonsCheckResult,
    MailReceived,
    MarksChanged,
    PollDue,
    ServiceKind,
    ServiceStatusChanged,
)

poll = PollDue(user_id=7, service=ServiceKind.BARS)
check(poll.target_queue == QUEUE_WORKER, "PollDue идёт в очередь worker")
payload = poll.to_payload()
check(payload["event_type"] == "poll.due", "у PollDue корректный event_type")
restored = PollDue.from_payload(payload)
check(
    restored.user_id == 7 and restored.event_id == poll.event_id,
    "PollDue переживает сериализацию",
)

marks = MarksChanged(user_id=7, changes=[{"subject": "X", "kind": "new_mark"}])
check(marks.target_queue == QUEUE_BOT, "MarksChanged идёт в очередь bot")
check(
    ServiceStatusChanged(service=ServiceKind.OSEP, is_up=True).event_type
    == "service.status_changed",
    "ServiceStatusChanged зарегистрирован",
)
check(
    Auth2FASubmitted(user_id=7, service=ServiceKind.BARS, code="1234").target_queue
    == QUEUE_WORKER,
    "код 2FA уходит в очередь worker",
)
check(
    set(EVENT_TYPES)
    == {
        "poll.due",
        "check.requested",
        "marks.changed",
        "absences.changed",
        "mail.received",
        "service.status_changed",
        "auth.2fa_required",
        "auth.2fa_code_resent",
        "auth.2fa_submitted",
        "auth.2fa_resend_requested",
        "auth.completed",
        "auth.2fa_failed",
        "auth.failed",
        "reminder.due",
        "lessons.check_requested",
        "lessons.check_result",
    },
    "каталог событий полон",
)
check(
    LessonsCheckResult.from_payload(
        LessonsCheckResult(user_id=7, issues=[{"kind": "missing"}]).to_payload()
    ).issues[0]["kind"]
    == "missing",
    "LessonsCheckResult roundtrip",
)
check(MailReceived.from_payload(MailReceived(user_id=1, folder="Входящие", mail={}).to_payload()).folder == "Входящие", "MailReceived roundtrip")

# 3. слияние настроек по-полевое (пример из решений: global 60/300, personal 600)
from src.services import DEFAULT_POLL_SETTINGS, merge_settings


class Row:
    def __init__(self, **kw):
        for name in (
            "poll_interval_seconds",
            "max_poll_interval_seconds",
            "request_timeout_seconds",
            "error_timeout_seconds",
            "auto_scale_enabled",
        ):
            setattr(self, name, kw.get(name))


global_row = Row(poll_interval_seconds=60, error_timeout_seconds=300)
override_row = Row(error_timeout_seconds=600)
merged = merge_settings(global_row, override_row)
check(merged.poll_interval_seconds == 60, "не указанное поле берётся из глобального")
check(merged.error_timeout_seconds == 600, "персональное поле перекрывает глобальное")
check(
    merged.max_poll_interval_seconds
    == DEFAULT_POLL_SETTINGS.max_poll_interval_seconds,
    "пропуски в обоих уровнях → дефолт",
)
check(merge_settings(None, None) == DEFAULT_POLL_SETTINGS, "без строк → дефолты")

# 4. diff оценок/пропусков и счётчиков папок
from src.services import (
    apply_hidden_mark,
    diff_bars_absences,
    diff_bars_marks,
    diff_folder_counts,
)


def km(mark: str, date: str = "", rewriting: list | None = None) -> dict:
    return {"mark": mark, "date": date, "rewriting": rewriting or []}


def disc(marks: list, final: str = "", pa: str = "") -> dict:
    return {"marks": marks, "mark_final": final, "mark_pa": pa}


old = {"Мат": disc([km(""), km("4")]), "Физ": disc([km("5")], final="5")}
new = {"Мат": disc([km("5", "08.09.26"), km("5")]), "Физ": disc([km("5")], final="5")}

changes = diff_bars_marks(old, new)
check(
    changes
    == [
        {
            "subject": "Мат",
            "kind": "new_mark",
            "km": 1,
            "old": None,
            "new": "5",
            "date": "08.09.26",
        },
        {
            "subject": "Мат",
            "kind": "mark_changed",
            "km": 2,
            "old": "4",
            "new": "5",
            "date": "",
        },
    ],
    "diff: новая оценка и смена оценки",
)

rewrite_new = {
    "Мат": disc(
        [
            km(""),
            km(
                "4",
                "",
                [
                    {"mark": "3", "date": "20.09.26"},
                    {"mark": "5", "date": "01.10.26", "not_counted": True},
                ],
            ),
        ]
    )
}
rewrite_changes = diff_bars_marks({"Мат": disc([km(""), km("4")])}, rewrite_new)
check(
    rewrite_changes
    == [
        {
            "subject": "Мат",
            "kind": "rewriting",
            "km": 2,
            "old": "4",
            "new": "3",
            "date": "20.09.26",
            "attempt": 1,
            "not_counted": False,
        },
        {
            "subject": "Мат",
            "kind": "rewriting",
            "km": 2,
            "old": "4",
            "new": "5",
            "date": "01.10.26",
            "attempt": 2,
            "not_counted": True,
        },
    ],
    "diff: переписывания (несколько штук, «Не учитывается» с флагом)",
)

final_changes = diff_bars_marks(
    {"Мат": disc([km("5")])}, {"Мат": disc([km("5")], final="4")}
)
check(
    final_changes
    == [
        {
            "subject": "Мат",
            "kind": "final_changed",
            "km": None,
            "old": None,
            "new": "4",
            "date": None,
        }
    ],
    "diff: смена итоговой оценки",
)
check(diff_bars_marks(None, new) == [], "первый снимок не порождает уведомлений")
removed = diff_bars_marks({"Мат": disc([km("4")])}, {"Мат": disc([km("")])})
check(
    len(removed) == 1
    and removed[0]["kind"] == "mark_changed"
    and removed[0]["old"] == "4"
    and removed[0]["new"] is None,
    "оценку поставили и убрали → mark_changed «4 → .»",
)

check(
    diff_bars_marks({"Мат": disc([km("4")])}, {"Мат": {"marks": [], "score": ""}})
    == [],
    "неразобранная страница (marks=[]) не считается изменением",
)

check(
    diff_bars_marks(
        {"Мат": disc([km("4")])},
        {"Мат": disc([km("4")]), "Новая дисциплина": disc([km("5")])},
    )
    == [],
    "новая дисциплина игнорируется (в начале семестра список дополняется)",
)

check(
    diff_bars_marks(
        {"_semester": "2025/2026, Весенний семестр", "Мат": disc([km("5")])},
        {"_semester": "2026/2027, Осенний семестр", "Мат": disc([km("")])},
    )
    == [],
    "смена семестра: «5 → .» по совпадающей дисциплине не уведомляется",
)
check(
    diff_bars_marks(
        {"_semester": "2026/2027, Осенний семестр", "Мат": disc([km("5")])},
        {"_semester": "2026/2027, Осенний семестр", "Мат": disc([km("4")])},
    )
    == [
        {
            "subject": "Мат",
            "kind": "mark_changed",
            "km": 1,
            "old": "5",
            "new": "4",
            "date": "",
        }
    ],
    "в одном семестре изменение оценки уведомляется как раньше",
)

masked = apply_hidden_mark(changes)
check(
    masked[0]["hidden"] is True and masked[0]["new"] is None,
    "скрытие оценки маскирует значения",
)

absence_row = {"lesson": "13.11.25, 3 пара, ...", "created": "17.12.25", "changed": ""}
absence_changes = diff_bars_absences(
    {"1": dict(absence_row)},
    {"1": dict(absence_row), "2": dict(absence_row, lesson="24.09.26, 5 пара, ...")},
)
check(
    absence_changes
    == [
        {
            "kind": "absence_new",
            "key": "2",
            "lesson": "24.09.26, 5 пара, ...",
            "created": "17.12.25",
            "changed": "",
        }
    ],
    "diff: новый пропуск",
)
check(
    apply_hidden_mark(absence_changes) == absence_changes,
    "скрытие оценки не трогает пропуски",
)

check(
    diff_bars_absences(
        {"_semester": "2025/2026, Весенний семестр", "1": dict(absence_row)},
        {"_semester": "2026/2027, Осенний семестр"},
    )
    == [],
    "смена семестра: старые пропуски не считаются снятыми",
)

grown = diff_folder_counts(
    {"Входящие": 5, "Отправленные": 3},
    {"Входящие": 7, "Отправленные": 9, "Архив": 1},
    ignore=frozenset({"Отправленные"}),
)
check(grown == {"Входящие": 2}, "diff папок игнорирует служебные и видит рост")

# 5. sim-клиенты реализуют порты; поток 2FA и снимки работают
import asyncio

from src.integrations import (
    AbsenceRecord,
    AuthStatus,
    BarsClient,
    DisciplineMarks,
    OsepClient,
    get_bars_client,
    get_osep_client,
    marks_to_snapshot,
)

check(isinstance(get_bars_client(1), BarsClient), "sim БАРС реализует порт BarsClient")
check(isinstance(get_osep_client(1), OsepClient), "sim ОСЭП реализует порт OsepClient")


async def sim_2fa_flow():
    # напрямую SimBarsClient: тест sim-контракта не должен зависеть от
    # INTEGRATION_MODE (фабрика вернула бы реальный клиент в mode=real)
    from src.integrations.sim import SimBarsClient

    client = SimBarsClient(42)
    result = await client.authenticate("2fa-user", "pw")
    send = await client.send_2fa_code()
    bad = await client.verify_2fa_code("0000")
    ok = await client.verify_2fa_code("1234")
    return result, send, bad, ok, await client.fetch_marks(), await client.fetch_absences()


result, send, bad, ok, marks, absences = asyncio.run(sim_2fa_flow())
check(
    result.status == AuthStatus.TWO_FACTOR_REQUIRED,
    "sim: вход с 2FA требует код",
)
check(bool(send.destination), "sim: destination кода известен")
check(bad.status == AuthStatus.CODE_INVALID, "sim: неверный код отклонён")
check(ok.status == AuthStatus.OK, "sim: верный код принят")
check(
    all(isinstance(values, DisciplineMarks) for values in marks.values()),
    "fetch_marks возвращает DisciplineMarks (не dict)",
)
check(
    all(isinstance(values, AbsenceRecord) for values in absences.values()),
    "fetch_absences возвращает AbsenceRecord (не dict)",
)
check(
    diff_bars_marks(None, marks_to_snapshot(marks)) == [],
    "снимок оценок sim совместим с diff",
)

def notification_checks() -> None:
    from src.bot.notifier import _format_event_html as format_event
    from src.bot.notifier import split_message
    from src.events.contracts import MarksChanged

    parts = split_message("x" * 5012)
    check(
        len(parts) == 2 and len(parts[0]) == 4000 and len(parts[1]) == 1012,
        "длинное письмо разбито с запасом (4000 + остаток)",
    )

    removal = MarksChanged(
        user_id=1,
        changes=[
            {
                "subject": "Мат, Иванов И.И., экзамен",
                "kind": "mark_changed",
                "km": 1,
                "old": "5",
                "new": None,
                "date": "",
            }
        ],
    )
    check("5 → ." in format_event(removal), "удаление оценки — «5 → .»")

    appeared = MarksChanged(
        user_id=1,
        changes=[
            {
                "subject": "Мат, Иванов И.И., экзамен",
                "kind": "new_mark",
                "km": 1,
                "old": None,
                "new": "5",
                "date": "2026-09-21",
            }
        ],
    )
    check("📗 Новая оценка" in format_event(appeared), "появление — «Новая оценка»")


notification_checks()

print("\nЯдро: smoke пройден ✅")
