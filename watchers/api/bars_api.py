import json
import re
from datetime import datetime

from loguru import logger

from watchers.api.base_api import BaseAPI
from watchers.auth.bars_auth import BarsAuth
from watchers.core.exceptions import DataParsingError, StudentIdGettingError
from watchers.models.mark_models import DisciplineMarks, Mark, RewritingMark
from watchers.utils.rate_limiter import RateLimiter

_rate_limit_fetcher = RateLimiter(max_requests=30, period_seconds=1)


class BarsAPI(BaseAPI):
    """API методы для БАРС (оценки)"""

    BASE_URL = "https://bars.mpei.ru/bars_web"

    def __init__(self, auth: BarsAuth):
        super().__init__(auth, self.BASE_URL)
        self._student_id: str | None = None

    async def get_student_id(self) -> str:
        if self._student_id:
            return self._student_id
        content = (await self._request_with_authorization(endpoint="/ST/Student/ListStudent")).decode()
        student_id = re.search(
            r'studentID=([\w]{8}-[\w]{4}-[\w]{4}-[\w]{4}-[\w]{12})',
            content
        )
        if not student_id:
            raise StudentIdGettingError(
                "Ошибка получения параметра student_id",
                content=content
            )
        self._student_id = student_id.group(1)
        return self._student_id

    @staticmethod
    def get_semester_id():
        autumn_25_26 = 26
        year = 2025
        autumn = 9 <= datetime.now().month or datetime.now().month <= 1
        if autumn:
            return (datetime.now().year - year if datetime.now().month >= 9 else datetime.now().year - year - 1) * 2 + autumn_25_26
        else:
            return (datetime.now().year - year) * 2 - 1 + autumn_25_26

    @_rate_limit_fetcher
    async def get_marks(self) -> str:
        student_id = await self.get_student_id()
        semester_id = self.get_semester_id()
        query = {
            "ID": student_id,
            "FilterSemester": {
                "Value": semester_id
            }
        }
        params = {'studentID': student_id, "query": json.dumps(query)}
        return (await self._request_with_authorization(
            endpoint="/ST_Study/Student_SemesterSheet/_PartialListStudent_SemesterSheet__Mark",
            params=params
        )).decode()

    @staticmethod
    def parse_marks(html) -> dict[str, DisciplineMarks]:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, 'html.parser')
        disciplines = soup.find_all("div", attrs={"class": "my-2"})
        data = {}

        for discipline in disciplines:
            try:
                name_text = discipline.text.strip()
                name_of_discipline = ",".join(name_text.split(",")[:3])

                if not name_of_discipline:
                    continue

                link_elem = discipline.find("a", attrs={"role": "button"})
                if not link_elem:
                    continue

                href = link_elem.get("href")
                if not href or not href.startswith("#"):
                    continue

                table_id = href[1:]
                table_div = soup.find("div", attrs={"id": table_id})
                if not table_div:
                    continue

                table = table_div.find('table')
                if not table:
                    continue

                tbody = table.find("tbody")
                if not tbody:
                    continue

                rows = tbody.find_all("tr")
                if not rows:
                    continue

                marks = []
                km_index = 1
                for row in rows:
                    cells = row.find_all("td")
                    if len(cells) != 4:
                        continue

                    km_name_text = cells[0].text.strip()

                    try:
                        weight = cells[1].text.strip()
                        if not weight.isdigit():
                            weight = "0"

                        date_of_event = cells[2].text.strip()
                        mark_text = cells[3].text.strip()

                        if mark_text:
                            parts = mark_text.split("(")
                            mark_value = parts[0].strip()
                            date_of_mark = parts[1].replace(")", "").strip().split()[0] if len(parts) > 1 else ""

                            mark = Mark(mark=mark_value, date=date_of_mark)

                            spans = cells[3].find_all("span")
                            attempt = 1
                            for span in spans:
                                rewrite_text = span.text.strip()
                                if rewrite_text:
                                    rewrite_parts = rewrite_text.split("(")
                                    if len(rewrite_parts) >= 2:
                                        rewrite_mark = rewrite_parts[0].strip().split()[-1]
                                        rewrite_date = rewrite_parts[1].replace(")", "").strip()
                                        mark.rewriting.append(
                                            RewritingMark(mark=rewrite_mark, date=rewrite_date, attempt=attempt)
                                        )
                                        attempt += 1
                        else:
                            mark = Mark(mark="", date=None)

                        marks.append(mark)
                        km_index += 1

                    except Exception as e:
                        logger.warning(f"Ошибка парсинга строки оценки в '{name_of_discipline}': {e}")
                        continue

                mark_PA = ""
                mark_final = ""

                for row in rows[-3:]:
                    cells = row.find_all("td")
                    if len(cells) >= 2:
                        text = cells[0].text.strip() if len(cells) > 0 else ""
                        value = cells[-1].text.strip() if len(cells) > 0 else ""

                        if "Промежуточная" in text:
                            mark_PA = value
                        elif "Итоговая" in text:
                            mark_final = value

                data[name_of_discipline] = DisciplineMarks(
                    name=name_of_discipline,
                    marks=marks,
                    mark_PA=mark_PA,
                    mark_final=mark_final
                )

            except Exception as e:
                logger.warning(f"Ошибка парсинга дисциплины: {e}")
                continue

        return data
