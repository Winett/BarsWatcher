"""BarsWebClient — реальный клиент БАРС по порту BarsClient."""

from __future__ import annotations

from datetime import date

from loguru import logger

from src.integrations.models import (
    AbsenceRecord,
    DisciplineMarks,
    LessonInfo,
    ScheduleItem,
    StudentProfile,
    TwoFASendResult,
)
from src.integrations.ports import (
    AuthError,
    AuthResult,
    AuthStatus,
    TwoFactorRequiredError,
)

from .api import BarsApi
from .auth import BarsAuth, SessionExpired
from .parser import (
    parse_absences,
    parse_leaders,
    parse_lessons,
    parse_marks,
    parse_schedule,
    parse_student_brief,
    parse_study_group,
    parse_training_journal_id,
)

__all__ = ["BarsWebClient"]

log = logger.bind(component="bars-client")


class BarsWebClient:
    """Реальный клиент БАРС: вход/2FA, оценки, пропуски, доступность.

    Состояние сессии (куки + ожидающее 2FA) переживает перезапуск через
    export_session()/restore_session() — worker хранит его в bars_states.
    """

    def __init__(self, timeout: float = 30.0):
        self._auth = BarsAuth(timeout=timeout)
        self._api = BarsApi(self._auth)
        self._login: str | None = None
        self._password: str | None = None

    async def close(self) -> None:
        await self._auth.close()

    # ───────────────────────────── сессия ─────────────────────────────

    def export_session(self) -> dict:
        return self._auth.export_session()

    def restore_session(self, session: dict | None) -> None:
        self._auth.restore_session(session)

    async def is_authenticated(self) -> bool:
        return await self._auth.is_authenticated()

    # ───────────────────────────── авторизация ─────────────────────────────

    async def authenticate(self, login: str, password: str) -> AuthResult:
        self._login, self._password = login, password
        return await self._auth.authenticate(login, password)

    async def send_2fa_code(self) -> TwoFASendResult:
        """Отправить код 2FA (destination — «Сообщение отправлено в ...»)."""
        return await self._auth.send_2fa_code()

    async def verify_2fa_code(self, code: str) -> AuthResult:
        ok = await self._auth.verify_2fa_code(code)
        return AuthResult(AuthStatus.OK if ok else AuthStatus.CODE_INVALID)

    async def check_availability(self) -> bool:
        return await self._auth.check_availability()

    # ───────────────────────────── данные ─────────────────────────────

    async def fetch_marks(self) -> dict[str, DisciplineMarks]:
        student_id = await self._student_id()
        return await self._with_relogin(
            lambda: self._api.get_marks_page(student_id, self._api.semester_id()),
            parse_marks,
        )

    async def fetch_absences(self) -> dict[str, AbsenceRecord]:
        student_id = await self._student_id()
        return await self._with_relogin(
            lambda: self._api.get_absences_page(student_id, self._api.semester_id()),
            parse_absences,
        )

    async def fetch_profile(self) -> StudentProfile:
        brief = await self._with_relogin(self._api.get_student_page, parse_student_brief)
        group = await self._with_relogin(self._api.get_study_group_page, parse_study_group)
        responsible = False
        if group.sg_id:
            leaders = await self._with_relogin(
                lambda: self._api.get_leaders_page(group.sg_id), parse_leaders
            )
            responsible = any(
                login.lower() == (self._login or "").lower() for login in leaders
            )
        return StudentProfile(
            fio=brief.fio,
            group=group.name,
            is_journal_responsible=responsible,
        )

    # ─────────────── напоминания о парах (для будущей фичи) ───────────────

    async def fetch_today_lessons(self, day: date | None = None) -> list[LessonInfo]:
        group = await self._with_relogin(self._api.get_study_group_page, parse_study_group)
        if not group.sg_id:
            raise AuthError("не удалось определить учебную группу")
        journals = await self._with_relogin(
            lambda: self._api.get_training_journals_page(group.sg_id),
            lambda html: parse_training_journal_id(html, self._api.semester_title()),
        )
        if journals is None:
            raise AuthError("не удалось определить учебный журнал текущего семестра")
        return await self._with_relogin(
            lambda: self._api.get_lessons_page(journals, day), parse_lessons
        )

    async def fetch_lessons_range(
        self,
        date_from: date,
        date_to: date,
        *,
        page_size: int = 500,
        max_pages: int = 20,
    ) -> list[LessonInfo]:
        """Занятия журнала за период: пагинация page_size, докачка страниц."""
        group = await self._with_relogin(self._api.get_study_group_page, parse_study_group)
        if not group.sg_id:
            raise AuthError("не удалось определить учебную группу")
        journals = await self._with_relogin(
            lambda: self._api.get_training_journals_page(group.sg_id),
            lambda html: parse_training_journal_id(html, self._api.semester_title()),
        )
        if journals is None:
            raise AuthError("не удалось определить учебный журнал текущего семестра")

        result: list[LessonInfo] = []
        for page in range(1, max_pages + 1):
            rows = await self._with_relogin(
                lambda p=page: self._api.get_lessons_page(
                    journals,
                    date_from=date_from,
                    date_to=date_to,
                    page=p,
                    page_size=page_size,
                ),
                parse_lessons,
            )
            result.extend(rows)
            if len(rows) < page_size:
                break
        return result

    async def fetch_schedule(
        self, date_from: date, date_to: date, group_name: str | None = None
    ) -> list[ScheduleItem]:
        if group_name is None:
            group = await self._with_relogin(
                self._api.get_study_group_page, parse_study_group
            )
            group_name = group.name
        if not group_name:
            raise AuthError("не удалось определить название группы")
        return await self._with_relogin(
            lambda: self._api.get_schedule_page(group_name, date_from, date_to),
            lambda html: parse_schedule(html, date_from, date_to),
        )

    # ───────────────────────────── внутреннее ─────────────────────────────

    async def _student_id(self) -> str:
        brief = await self._with_relogin(self._api.get_student_page, parse_student_brief)
        if not brief.student_id:
            raise AuthError("не удалось получить studentID")
        return brief.student_id

    async def _with_relogin(self, request, parse):
        """Запрос с одним повтором после перелогина (302 на страницу входа)."""
        try:
            html = await request()
        except SessionExpired as error:
            if not (self._login and self._password):
                raise AuthError("сессия слетела, креды недоступны") from error
            log.info("БАРС: сессия слетела, перелогин | login={}", self._login)
            result = await self._auth.authenticate(self._login, self._password)
            if result.status == AuthStatus.TWO_FACTOR_REQUIRED:
                # у пользователя включён 2FA — нужен новый код
                raise TwoFactorRequiredError("перелогин потребовал 2FA") from None
            if result.status != AuthStatus.OK:
                raise AuthError("перелогин не удался") from None
            try:
                html = await request()
            except SessionExpired as retry_error:
                raise AuthError("перелогин не восстановил сессию") from retry_error
        return parse(html)
