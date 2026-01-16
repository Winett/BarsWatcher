from typing import Dict, List, Optional


from watchers.core.base_watcher import BaseWatcher
from watchers.models.watcher_models import WatcherType, UserCredentials, WatcherConfig
from watchers.models.mark_models import DisciplineMarks
from loguru import logger

from watchers.services.cache_service import AsyncFileCacher, FileCache
from watchers.fetchers.bars_fetcher import BarsFetcher
from watchers.services.notification_service import BaseNotificationService

from hashlib import md5

from watchers.managers.watcher_manager import BarsWatcherManager


class BarsWatcher(BaseWatcher):
    def __init__(self,
                 credentials: UserCredentials,
                 # fetcher_service: BarsFetcher,
                 cache_service: AsyncFileCacher,
                 # notification_service: BaseNotificationService,
                 config: Optional[WatcherConfig] = None,
                 student_id: Optional[str] = None,
                 ):
        super().__init__(credentials, cache_service,
                         # notification_service,
                         config)
        self.fetcher_service = BarsFetcher(credentials, student_id=student_id)
        # self.marks_url_endpoint = "/ST_Study/Student_SemesterSheet/_PartialListStudent_SemesterSheet__Mark"
        # self.student_id_url_endpoint = "/ST/Student/ListStudent"
        self._student_id: Optional[str] = student_id

        self._last_process_data = None
        self._last_process_data_hash = None

    def _register_instance(self):
        BarsWatcherManager.register_watcher(self.credentials.user_id, self)

    async def fetch_data(self) -> str | bytes:
        return await self.fetcher_service.get_bars_marks()

    async def process_data(self, data: str | bytes) -> dict[str, DisciplineMarks]:
        new_hash = md5(data.encode(errors="ignore")).hexdigest()
        if self._last_process_data_hash:
            if new_hash == self._last_process_data_hash:
                return self._last_process_data

        self._last_process_data = self.fetcher_service.parse_marks(data)
        self._last_process_data_hash = new_hash

        return self._last_process_data

    async def _iteration(self):
        logger.debug(f"{self._logger_template} Начало итерации")
        logger.info(f"{self._logger_template} {self.student_id = } {self._last_process_data_hash = }")
        await super()._iteration()

    async def detect_changes(self, old_data: dict, new_data: dict) -> List[str]:
        """Обнаружение изменений в оценках"""
        if not old_data or not new_data or old_data is new_data:
            return []

        old_data = {k: DisciplineMarks(**v) for k, v in old_data.items()}

        changes = []
        for discipline_name, new_discipline in new_data.items():
            old_discipline = old_data.get(discipline_name)

            if not old_discipline:
                # changes.append(f"Добавлена дисциплина {discipline_name}")
                continue

            changes.extend(self._compare_disciplines(old_discipline, new_discipline))

        return changes

    def _compare_disciplines(self, old: DisciplineMarks, new: DisciplineMarks) -> list[str]:
        """Сравнение двух дисциплин"""
        changes = []

        for i, (old_mark, new_mark) in enumerate(zip(old.marks, new.marks)):
            if old_mark.mark != new_mark.mark:
                if not old_mark.mark:
                    changes.append(f"Оценка по {old.name} КМ-{i + 1}: {new_mark.mark}")
                else:
                    changes.append(f"Изменилась оценка по {old.name} КМ-{i + 1}: {old_mark.mark} -> {new_mark.mark}")

            # Сравнение переписываний
            if len(old_mark.rewriting) != len(new_mark.rewriting):
                for rewrite in range(1, len(new_mark.rewriting) - len(old_mark.rewriting) + 1):
                    changes.append(f"Переписывание по {old.name} КМ-{i + 1}: -> {new_mark.rewriting[-rewrite].mark}") # Последние оценки - последние переписывания

            for j in range(min(len(old_mark.rewriting), len(new_mark.rewriting))):
                if old_mark.rewriting[j].mark != new_mark.rewriting[j].mark:
                    changes.append(f"Переписывание по {old.name} КМ-{i + 1}: {old_mark.rewriting[j].mark} -> {new_mark.rewriting[j].mark}")


        # Сравнение итоговых оценок
        if old.mark_final != new.mark_final:
            changes.append(f"Изменилась итоговая оценка по {old.name}: {old.mark_final} -> {new.mark_final}")

        return changes

    @property
    def student_id(self) -> Optional[str]:
        return self.fetcher_service._student_id

    async def _get_student_id(self) -> str:
        """Получение student_id"""
        return await self.fetcher_service.get_student_id()

    async def close(self):
        await self.fetcher_service.close()

