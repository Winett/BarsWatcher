from __future__ import annotations

from loguru import logger

from watchers.auth.bars import BarsAuth
from watchers.auth.utils import extract_student_id
from watchers.base import BaseWatcher
from watchers.fetcher.bars_fetcher import BarsFetcher

from watchers.connection.base import BarsConnectionMonitor
from watchers.fetcher.exceptions import AuthError
from watchers.notificator.base import BaseNotificator
from watchers.notificator.model import NotificatorMessage, WatcherType
from watchers.parser.base import BarsMarkParser

from watchers.cacher import FileCacher, BaseCacher

from watchers.models.mark import Mark



class BarsWatcher(BaseWatcher):
    marks_url = "https://bars.mpei.ru/bars_web/ST_Study/Student_SemesterSheet/_PartialListStudent_SemesterSheet__Mark"
    student_id_url = "https://bars.mpei.ru/bars_web/ST/Student/ListStudent"

    def __init__(
        self,
        username: str,
        password: str,
        user_id: int,
        notifier: BaseNotificator,
        casher: BaseCacher = FileCacher(),
    ) -> None:
        super().__init__(username, password, user_id, BarsAuth, BarsFetcher, BarsConnectionMonitor, BarsMarkParser)
        self.notifier = notifier
        self.cacher = casher
        self._template_cache = f"{self.__class__.__name__}_{self.username}"

        self._student_id = None

    @property
    def student_id(self) -> str | None:
        return self._student_id

    async def notify(self, message: str):
        logger.debug(self._logger_template + f"{message}")
        message = NotificatorMessage(message=message, user_id=self.user_id, watcher=WatcherType.BARS)
        await self.notifier.notify(message)

    # async def notify(self, messages: list[str]):
    #     # logger.debug(self._logger_template + f"{messages}")
    #     msgs = []
    #     for message in messages:
    #         msgs.append(NotificatorMessage(message=message, user_id=self.user_id, watcher=WatcherType.BARS))
    #         logger.debug(self._logger_template + f"{message}")
    #     for msg in msgs:
    #         await self.notifier.notify(msg)



    async def _fetch_and_process_data(self):
        #TODO: Получать student_id сразу при логине в БАРС
        student_id = await self._get_student_id()
        kwargs = {
            'studentID': student_id,
            # "query": json.dumps({
            #     "ID": student_id,
            #     "FilterSemester": datetime.now().year % 100
            # })
        }
        data = await self.fetcher.fetch(self.marks_url, params=kwargs)
        old_data = self.cacher.get(self._template_cache, Mark)
        #AttributeError - обработать при методе parse
        parse_data = self.parser(data).parse()
        changes = self.compare_data(old_data, parse_data)
        if old_data is None:
            self.cacher.set(self._template_cache, parse_data)
        if changes:
            self.cacher.set(self._template_cache, parse_data)
            await self.notify('\n'.join(changes))


    async def test_login(self):
        result = await self.auth.login()
        return result

    async def start_watching(self) -> None:
        auth = await self.auth.login()
        if not auth:
            raise AuthError("Не удалось авторизоваться")
        await self._get_student_id()
        await super().start_watching()


    @staticmethod
    def compare_data(old_data: dict[str, Mark], new_data: dict[str, Mark]):
        if not old_data:
            return []
        changes = []
        for discipline in new_data:
            if discipline not in old_data:
                changes.append(f"Добавлена дисциплина {discipline}")
                continue
            if len(new_data[discipline].marks) != len(old_data[discipline].marks):
                changes.append(f"Изменилось количество КМ по {discipline}")
                continue

            for i in range(len(new_data[discipline].marks)):
                old_mark = old_data[discipline].marks[i]
                new_mark = new_data[discipline].marks[i]
                if old_mark.mark != new_mark.mark:
                    #Если изменили основную оценку
                    changes.append(f"Изменилась оценка по {discipline} КМ-{i+1}: {old_mark.mark} -> {new_mark.mark}")
                if len(old_mark.rewriting) != len(new_mark.rewriting):
                    #Если поставили оценку за есколько переписываний
                    for j in range(len(new_mark.rewriting) - len(old_mark.rewriting)):
                        changes.append(f"Поставлена оценка за переписывание по {discipline} КМ-{i+1}: {new_mark.rewriting[-(j + 1)].mark}")
                else:
                    for j in range(len(new_mark.rewriting)):
                        old_rewrite_mark = old_mark.rewriting[j]
                        new_rewrite_mark = new_mark.rewriting[j]
                        if old_rewrite_mark.mark != new_rewrite_mark.mark:
                            changes.append(f"Изменилась оценка за переписывание по {discipline} КМ-{i+1}: {old_rewrite_mark.mark} -> {new_rewrite_mark.mark}")


            if old_data[discipline].mark_PA != new_data[discipline].mark_PA:
                changes.append(f"Изменение оценки ПА по {discipline}: {old_data[discipline].mark_PA} -> {new_data[discipline].mark_PA}")
            if old_data[discipline].mark_final != new_data[discipline].mark_final:
                changes.append(f"Изменение Итоговой оценки по {discipline}: {old_data[discipline].mark_final} -> {new_data[discipline].mark_final}")

        return changes

    async def _get_student_id(self) -> str | None:
        if self._student_id:
            return self._student_id

        session = await self.auth.get_session()
        async with session.get(self.student_id_url, allow_redirects=False) as response:
            content = await response.read()

        self._student_id = extract_student_id(content)
        return self._student_id

