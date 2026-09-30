"""Sim-реализации клиентов БАРС/ОСЭП.

Позволяют прогнать всё ядро (планировщик → проверки → diff → события) без
доступа к реальным сервисам.

**SimBarsClient делает «настоящие» запросы**: каждый fetch читает сохранённый
HTML-ответ сервера из simdata/bars/*.html и прогоняется через реальные
парсеры. Правки в этих файлах видны как обычные изменения данных
(diff → события → оповещения) — так удобно тестировать уведомления.

**SimOsepClient** — случайная симуляция почты (письма приходят сами).

Договорённости sim-режима (для ручного тестирования):
  - пароль "wrong"            → INVALID_CREDENTIALS;
  - логин, начинается с "2fa" → требуется 2FA (код "0000" — неверный, любой
    другой — верный; после verify_2fa_code OK сессия становится активной);
  - SIM_DOWN=1 (env) или simdata/service.json → сервис считается недоступным.

Возвращаемые данные — те же модели, что и у реального клиента (integrations.models).
"""

from __future__ import annotations

import os
import random
from datetime import date
from pathlib import Path

from .bars import parser
from .file_sim import service_up
from .models import (
    AbsenceRecord,
    Attachment,
    DisciplineMarks,
    LessonInfo,
    MailMessage,
    ScheduleItem,
    StudentProfile,
    TwoFASendResult,
)
from .ports import AuthError, AuthResult, AuthStatus, DataParseError

__all__ = ["SimBarsClient", "SimOsepClient"]

_FOLDERS = {"Входящие": 12, "Архив": 4, "Учёба": 3}

# имена сохранённых ответов БАРС (simdata/bars/)
RESPONSE_LOGIN = "bars_web.html"
RESPONSE_2FA = "bars_web.after_login_and_2fa_required.html"
RESPONSE_PROFILE = "bars_web.ST.Student._PartialListStudent.html"
RESPONSE_MARKS = (
    "bars_web.ST_Study.Student_SemesterSheet."
    "_PartialListStudent_SemesterSheet__Mark.html"
)
RESPONSE_ABSENCES = (
    "bars_web.ST_Study.Lesson__Student._PartialListStudent__Lesson__Student.html"
)
RESPONSE_LEADERS = "bars_web.SG.StudyGroupLeader._PartialListStudyGroup_Leaders.html"
RESPONSE_GROUP = "bars_web.SG.StudyGroup._PartialListStudyGroup.html"
RESPONSE_LESSONS = "bars_web.SG.Lesson._PartialListTrainingJournal_Lessons.html"
RESPONSE_SCHEDULE = "bars_web.Open.RUZ._PartialTimetable.html"

# ── разделяемое состояние симуляции (память процесса) ─────────────────────────
_auth: dict[str, bool] = {}
_folders: dict[str, dict[str, int]] = {}
_mail_seq: dict[str, int] = {}


def _response_dir() -> Path:
    base = os.environ.get("SIM_DATA_DIR")
    if base:
        return Path(base) / "bars"
    # simdata/ в корне проекта (рядом с src/)
    return Path(__file__).resolve().parents[2] / "simdata" / "bars"


def _read_response(name: str) -> str:
    """Прочитать сохранённый HTML-ответ сервера («настоящий» запрос)."""
    path = _response_dir() / name
    if not path.exists():
        raise DataParseError(
            f"sim: нет файла ответа {path} — сохраните туда HTML-ответ БАРС"
        )
    return path.read_text(encoding="utf-8", errors="replace")


class SimBarsClient:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self._key = f"bars:{user_id}"
        self._login = ""
        self._pending_2fa = False

    # ── сессия ─────────────────────────────────────────────────────────

    def export_session(self) -> dict:
        return {
            "cookies": {"ses_bars": "sim"} if _auth.get(self._key) else {},
            "pending_2fa": {"account": self._login} if self._pending_2fa else None,
        }

    def restore_session(self, session: dict | None) -> None:
        if not session:
            return
        cookies = session.get("cookies") or {}
        if cookies.get("ses_bars"):
            _auth[self._key] = True
        pending = session.get("pending_2fa") or {}
        # приоритет — живое состояние клиента (не затираем свежий pending)
        if pending and not self._pending_2fa:
            self._pending_2fa = True
            self._login = pending.get("account", self._login)

    # ── авторизация ────────────────────────────────────────────────────

    async def is_authenticated(self) -> bool:
        return _auth.get(self._key, False)

    async def authenticate(self, login: str, password: str) -> AuthResult:
        self._login = login
        if password == "wrong":
            return AuthResult(AuthStatus.INVALID_CREDENTIALS)
        if login.lower().startswith("2fa"):
            self._pending_2fa = True
            return AuthResult(AuthStatus.TWO_FACTOR_REQUIRED)
        self._pending_2fa = False
        _auth[self._key] = True
        return AuthResult(AuthStatus.OK, profile=await self.fetch_profile())

    async def send_2fa_code(self) -> TwoFASendResult:
        return TwoFASendResult(
            success=True, destination="Сообщение отправлено в 'Telegram (sim)'"
        )

    async def verify_2fa_code(self, code: str) -> AuthResult:
        if code == "0000":
            return AuthResult(AuthStatus.CODE_INVALID)
        self._pending_2fa = False
        _auth[self._key] = True
        return AuthResult(AuthStatus.OK)

    # ── данные: настоящие HTML-ответы из simdata/bars/ ─────────────────

    async def fetch_profile(self) -> StudentProfile:
        brief = parser.parse_student_brief(_read_response(RESPONSE_PROFILE))
        group = parser.parse_study_group(_read_response(RESPONSE_GROUP))
        leaders = parser.parse_leaders(_read_response(RESPONSE_LEADERS))
        return StudentProfile(
            fio=brief.fio,
            group=group.name,
            # ответственный = вошли логином из списка ответственных
            is_journal_responsible=self._login in leaders,
        )

    async def fetch_marks(self) -> dict[str, DisciplineMarks]:
        if not await self.is_authenticated():
            raise AuthError("sim: нет активной сессии")
        return parser.parse_marks(_read_response(RESPONSE_MARKS))

    async def fetch_absences(self) -> dict[str, AbsenceRecord]:
        if not await self.is_authenticated():
            raise AuthError("sim: нет активной сессии")
        return parser.parse_absences(_read_response(RESPONSE_ABSENCES))

    async def fetch_today_lessons(self, day: date | None = None) -> list[LessonInfo]:
        if not await self.is_authenticated():
            raise AuthError("sim: нет активной сессии")
        return parser.parse_lessons(_read_response(RESPONSE_LESSONS))

    async def fetch_lessons_range(
        self, date_from: date, date_to: date, **_
    ) -> list[LessonInfo]:
        return await self.fetch_today_lessons()

    async def fetch_schedule(
        self,
        date_from: date,
        date_to: date,
        group_name: str | None = None,
    ) -> list[ScheduleItem]:
        if not await self.is_authenticated():
            raise AuthError("sim: нет активной сессии")
        return parser.parse_schedule(
            _read_response(RESPONSE_SCHEDULE), date_from, date_to
        )

    async def check_availability(self) -> bool:
        return service_up("bars")


class SimOsepClient:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self._key = f"osep:{user_id}"

    def export_session(self) -> dict:
        return {
            "cookies": {"ses_osep": "sim"} if _auth.get(self._key) else {},
            "pending_2fa": None,
        }

    def restore_session(self, session: dict | None) -> None:
        if session and (session.get("cookies") or {}).get("ses_osep"):
            _auth[self._key] = True
    async def is_authenticated(self) -> bool:
        return _auth.get(self._key, False)

    async def authenticate(self, login: str, password: str) -> AuthResult:
        if password == "wrong":
            return AuthResult(AuthStatus.INVALID_CREDENTIALS)
        _auth[self._key] = True
        return AuthResult(AuthStatus.OK)

    async def fetch_folder_counts(self) -> dict[str, int]:
        if not await self.is_authenticated():
            raise AuthError("sim: нет активной сессии")
        folders = _folders.setdefault(self._key, dict(_FOLDERS))
        if random.random() < 0.25:
            folders["Входящие"] += random.randint(1, 2)
        return dict(folders)

    async def fetch_new_mails(self, folder: str, count: int) -> list[MailMessage]:
        mails = []
        for _ in range(count):
            _mail_seq[self._key] = _mail_seq.get(self._key, 0) + 1
            n = _mail_seq[self._key]
            mails.append(
                MailMessage(
                    subject=f"[sim] Письмо №{n} в папке «{folder}»",
                    sender_name="Симулятор ОСЭП",
                    sender_email=f"sim{n % 5}@mpei.ru",
                    body=f"Тело тестового письма №{n}.",
                    has_attachments=n % 3 == 0,
                    attachments=(
                        [
                            Attachment(
                                filename=f"doc_{n}.txt",
                                content_type="text/plain",
                                size=128,
                                attachment_id=f"sim-att-{self._key}-{n}",
                            )
                        ]
                        if n % 3 == 0
                        else []
                    ),
                )
            )
        return mails

    async def download_attachment(self, attachment_id: str) -> bytes:
        return f"[sim-вложение {attachment_id}]".encode()

    async def check_availability(self) -> bool:
        return service_up("osep")
