from __future__ import annotations

from abc import ABC, abstractmethod
from watchers.models.mark import Mark, MarkKM, RewriteMark

from bs4 import BeautifulSoup
from dataclasses import dataclass
from json import loads



class BaseParser(ABC):

    def __init__(self, data: str | bytes):
        self.data = data

    @abstractmethod
    def parse(self):
        raise NotImplementedError

class BarsMarkParser(BaseParser):

    def parse(self):
        try:
            soup = BeautifulSoup(self.data, 'html.parser')
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
                        # Проверка что это КМ
                        # if not km_name_text or "КМ" not in km_name_text:
                        #     continue

                        try:
                            weight = cells[1].text.strip()
                            if not weight.isdigit():
                                weight = "0"

                            date_of_event = cells[2].text.strip()
                            mark_text = cells[3].text.strip()

                            if mark_text:
                                # Формат: "4  (04.10.25)"
                                parts = mark_text.split("(")
                                mark_value = parts[0].strip()
                                date_of_mark = parts[1].replace(")", "").strip().split()[0] if len(parts) > 1 else ""

                                mark = MarkKM(mark=mark_value, date_of_mark=date_of_mark)

                                # Проверка переписываний
                                spans = cells[3].find_all("span")
                                for span in spans:
                                    rewrite_text = span.text.strip()
                                    if rewrite_text:
                                        # Формат: "2 (14.03.24)"
                                        rewrite_parts = rewrite_text.split("(")
                                        if len(rewrite_parts) >= 2:
                                            rewrite_mark = rewrite_parts[0].strip().split()[-1]
                                            rewrite_date = rewrite_parts[1].replace(")", "").strip()
                                            mark.rewriting.append(
                                                RewriteMark(mark=rewrite_mark, date_of_mark=rewrite_date)
                                            )
                            else:
                                mark = MarkKM(mark="", date_of_mark="")

                            marks.append(mark)
                            km_index += 1

                        except Exception as e:
                            continue

                    # Получение итоговых оценок
                    mark_PA = ""
                    mark_final = ""

                    for row in rows[-3:]:  # Последние 3 строки
                        cells = row.find_all("td")
                        if len(cells) >= 2:
                            text = cells[0].text.strip() if len(cells) > 0 else ""
                            value = cells[-1].text.strip() if len(cells) > 0 else ""

                            if "Промежуточная" in text:
                                mark_PA = value
                            elif "Итоговая" in text:
                                mark_final = value

                    data[name_of_discipline] = Mark(
                        discipline=name_of_discipline,
                        marks=marks,
                        mark_PA=mark_PA,
                        mark_final=mark_final
                    )

                except Exception as e:
                    continue

            return data

        except Exception as e:
            return {}


@dataclass
class OsepNotificatorEvent:
    ConversationId: str | None = None
    ItemId: str | None = None

    EventType: str = "0"
    id: str = "NewMailNotification"

@dataclass
class AttachmentData:
    id: str
    content_type: str
    filename: str
    size: int

@dataclass
class Attachment:
    content: bytes
    filename: str




class OsepParserLongPolling(BaseParser):

    test_data = """"<script>{id:\'pg\',data:\'reinitSubscription\'}</script>\r\n<script></script>\r\n<script></script>\r\n<script>[{"EventType":"0","id":"NewMailNotification","ConversationId":"AAQkADBiMDM2ZmI3LTI0Y2ItNDMzMy05OWQ1LTRhY2Y0ZDFmYmNhNAAQALcpIspJ5bdDupv4BIdCKI8=","IsClutter":false,"ItemId":"AAMkADBiMDM2ZmI3LTI0Y2ItNDMzMy05OWQ1LTRhY2Y0ZDFmYmNhNABGAAAAAAAXF5gPgvkQRbR8chVGnnQxBwBf9fplkHqsS5Ua52t4fVokAAAAAAEMAABf9fplkHqsS5Ua52t4fVokAAIbn3sOAAA=","PreviewText":"\\u200b3123\\u000d\\u000a\\u000d\\u000a________________________________\\u000d\\u000a\\u041e\\u0442: \\u0410\\u043d\\u0442\\u0438\\u043f\\u043e\\u0432 \\u0412\\u0430\\u0434\\u0438\\u043c \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440\\u043e\\u0432\\u0438\\u0447\\u000d\\u000a\\u041e\\u0442\\u043f\\u0440\\u0430\\u0432\\u043b\\u0435\\u043d\\u043e: 23 \\u0434\\u0435\\u043a\\u0430\\u0431\\u0440\\u044f 2025 \\u0433. 14:36\\u000d\\u000a\\u041a\\u043e\\u043c\\u0443: \\u0410\\u043d\\u0442\\u0438\\u043f\\u043e\\u0432 \\u0412\\u0430\\u0434\\u0438\\u043c \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440\\u043e\\u0432\\u0438\\u0447\\u000d\\u000a\\u0422\\u0435\\u043c\\u0430: Test\\u000d\\u000a\\u000d\\u000a\\u000d\\u000aTest\\u200b\\u000d\\u000a","Sender":"\\u0410\\u043d\\u0442\\u0438\\u043f\\u043e\\u0432 \\u0412\\u0430\\u0434\\u0438\\u043c \\u0410\\u043b\\u0435\\u043a\\u0441\\u0430\\u043d\\u0434\\u0440\\u043e\\u0432\\u0438\\u0447","Subject":"Re: Test"}]</script>\r\n<script></script>\r\n<script></script>\r\n"""

    def parse(self):
        data = []

        soup = BeautifulSoup(self.data, 'html.parser')
        # print(f"{self.data = }")
        scripts = soup.find_all("script")
        for s in scripts:
            if s.text and not s.text.startswith("{id:"):
                data.extend(loads(s.text, strict=False))
        # print(f"{scripts = }")
        # print(f"{data = }")
        refresh_data = []
        for d in data:
            if d["EventType"] == "0":
                obj = OsepNotificatorEvent(EventType=d.get("EventType"), id=d.get("id"), ConversationId=d.get("ConversationId"), ItemId=d.get("ItemId"))
                refresh_data.append(obj)

        return refresh_data


if __name__ == '__main__':
    pr = OsepParserLongPolling(OsepParserLongPolling.test_data)
    pr.parse()
