"""HTTP-вызовы БАРС. 302 на страницу входа = сессия слетела (SessionExpired)."""

from __future__ import annotations

import json
from datetime import date

import httpx

from src.integrations.ports import ServiceUnavailableError

from .auth import BarsAuth, SessionExpired
from .parser import current_semester_title

__all__ = ["BarsApi"]

_QUERY_DEFAULT = {
    "ID": None,
    "State": None,
    "SortOrder": None,
    "Page": "",
    "PageSize": None,
    "SearchText": "",
}


class BarsApi:
    def __init__(self, auth: BarsAuth):
        self._auth = auth

    # ───────────────────────────── запросы ─────────────────────────────

    async def _get(self, endpoint: str, params: dict | None = None) -> str:
        try:
            response = await self._auth.client.get(endpoint, params=params)
        except httpx.HTTPError as error:
            raise ServiceUnavailableError(str(error)) from error
        if response.status_code >= 500 or response.status_code == 429:
            raise ServiceUnavailableError(f"{endpoint}: HTTP {response.status_code}")
        if response.status_code in (301, 302, 303, 307, 308):
            raise SessionExpired(f"redirect {response.status_code} на {endpoint}")
        return response.text

    # ───────────────────────────── профиль ─────────────────────────────

    async def get_student_page(self) -> str:
        return await self._get("/bars_web/ST/Student/ListStudent")

    async def get_study_group_page(self) -> str:
        query = dict(
            _QUERY_DEFAULT,
            SortOrder="sg_Course,sg_DisplayName",
            PageSize="10",
            FilterDisplayName="",
            FilterFormationYear="",
            FilterCourse="",
            FilterSubfacultyName="",
            FilterAE_OrganizationName="",
            FilterEmail="",
            FilterDegree={"Value": "*"},
            FilterTrainingForm={"Value": "*"},
            FilterInstitute={},
        )
        return await self._get(
            "/bars_web/SG/StudyGroup/_PartialListStudyGroup",
            params={"query": json.dumps(query, ensure_ascii=False)},
        )

    async def get_leaders_page(self, sg_id: str) -> str:
        query = dict(_QUERY_DEFAULT, SearchText="")
        return await self._get(
            "/bars_web/SG/StudyGroupLeader/_PartialListStudyGroup_Leaders",
            params={"sgID": sg_id, "query": json.dumps(query, ensure_ascii=False)},
        )

    # ───────────────────────────── оценки/пропуски ─────────────────────────────

    async def get_marks_page(self, student_id: str, semester_id: int | str | None = None) -> str:
        query: dict = {"ID": student_id}
        if semester_id is not None:
            query["FilterSemester"] = {"Value": str(semester_id)}
        return await self._get(
            "/bars_web/ST_Study/Student_SemesterSheet/_PartialListStudent_SemesterSheet__Mark",
            params={
                "studentID": student_id,
                "query": json.dumps(query, ensure_ascii=False),
            },
        )

    async def get_absences_page(
        self, student_id: str, semester_id: int | str | None = None
    ) -> str:
        query = {
            "ID": student_id,
            "State": None,
            "SortOrder": "CustomSort1 desc",
            "Page": "",
            "PageSize": "500",  # максимум: пропуск могут поставить задним числом
            "SearchText": "",
            "FilterStartDate": "",
            "FilterEndDate": "",
            # текущий журнал (семестр): «*» тянет пропуски всех семестров
            "FilterSemester": {"Value": str(semester_id) if semester_id else "*"},
            "FilterLessonSkipType": {"Value": "1"},  # только пропуски
            "FilterLessonSkipReason": {"Value": "*"},
        }
        return await self._get(
            "/bars_web/ST_Study/Lesson__Student/_PartialListStudent__Lesson__Student",
            params={
                "studentID": student_id,
                "query": json.dumps(query, ensure_ascii=False),
            },
        )

    # ─────────────── журнал группы / занятия / расписание (напоминания) ───────────────

    async def get_training_journals_page(self, sg_id: str) -> str:
        query = dict(_QUERY_DEFAULT, SearchText="")
        return await self._get(
            "/bars_web/SG/TrainingJournal/_PartialListStudyGroup__TrainingJournals",
            params={"sgID": sg_id, "query": json.dumps(query, ensure_ascii=False)},
        )

    async def get_lessons_page(
        self,
        tj_id: int,
        day: date | None = None,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        page: int = 1,
        page_size: int = 10,
    ) -> str:
        day = day or date.today()
        start_str = (date_from or day).strftime("%d.%m.%Y")
        end_str = (date_to or day).strftime("%d.%m.%Y")
        query = {
            "ID": str(tj_id),
            "State": None,
            "SortOrder": None,
            "Page": str(page),
            "PageSize": str(page_size),
            "SearchText": "",
            "FilterStartDate": start_str,
            "FilterEndDate": end_str,
            "DisplayModeFilter": {
                "Value": "LIS_1;LIS_2;LIS_3;LT_1;LT_2;LT_3;LT_4;1;"
            },
        }
        return await self._get(
            "/bars_web/SG/Lesson/_PartialListTrainingJournal_Lessons",
            params={"tjID": tj_id, "query": json.dumps(query, ensure_ascii=False)},
        )

    async def get_schedule_page(
        self,
        group_name: str,
        date_from: date,
        date_to: date,
        as_table: bool = False,
    ) -> str:
        """Расписание (открытый эндпоинт, авторизация не обязательна)."""
        return await self._get(
            "/bars_web/Open/RUZ/_PartialTimetable",
            params={
                "rt": "3",
                "name": group_name,
                "sd": date_from.strftime("%d.%m.%Y"),
                "ed": date_to.strftime("%d.%m.%Y"),
                "st": "1" if as_table else "2",
            },
        )

    # ───────────────────────────── прочее ─────────────────────────────

    @staticmethod
    def semester_id(today: date | None = None) -> int:
        """id семестра в БАРС: 28 — осень 2026/2027, 29 — весна 2026/2027 и т.д."""
        today = today or date.today()
        academic_year = today.year if today.month >= 9 else today.year - 1
        semester_offset = 0 if today.month >= 9 else 1
        return (academic_year - 2025) * 2 + 26 + semester_offset

    @staticmethod
    def semester_title(today: date | None = None) -> str:
        return current_semester_title(today)
