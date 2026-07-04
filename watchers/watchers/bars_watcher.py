from datetime import datetime
from typing import Dict, List, Optional

from hashlib import md5

from loguru import logger

from watchers.core.base_watcher import BaseWatcher
from watchers.models.watcher_models import UserCredentials, WatcherConfig, BarsWatcherConfig
from watchers.models.mark_models import DisciplineMarks
from watchers.services.cache_service import AsyncFileCacher
from watchers.api.bars_api import BarsAPI
from watchers.managers.watcher_manager import BarsWatcherManager
from watchers.core.exceptions import DataParsingError


class BarsWatcher(BaseWatcher):
    def __init__(
        self,
        credentials: UserCredentials,
        api: BarsAPI,
        cache_service: AsyncFileCacher,
        config: Optional[WatcherConfig] = None,
        config_service=None,
        bars_config: Optional[BarsWatcherConfig] = None,
    ):
        super().__init__(credentials, cache_service, config, config_service)
        self.api = api
        self.bars_config = bars_config or BarsWatcherConfig()
        self._last_process_data = None
        self._last_process_data_hash = None

    def _register_instance(self):
        BarsWatcherManager.register_watcher(self.credentials.user_id, self)
        logger.debug(f"{self._logger_template} Зарегистрирован в BarsWatcherManager")

    async def fetch_data(self) -> str:
        logger.debug(f"{self._logger_template} bars_api.get_marks()...")
        data = await self.api.get_marks()
        logger.debug(f"{self._logger_template} bars_api.get_marks() OK | size={len(data)} bytes")
        return data

    async def process_data(self, data: str) -> dict[str, DisciplineMarks]:
        new_hash = md5(data.encode(errors="ignore")).hexdigest()
        if self._last_process_data_hash:
            if new_hash == self._last_process_data_hash:
                logger.debug(f"{self._logger_template} Данные не изменились (hash={new_hash[:8]}...), кэш")
                return self._last_process_data
        logger.debug(f"{self._logger_template} Новый hash={new_hash[:8]}..., парсинг...")
        try:
            self._last_process_data = self.api.parse_marks(data)
        except Exception:
            raise DataParsingError(
                f"Ошибка парсинга данных у {self.credentials.username} <code>{self.credentials.user_id}</code>",
                content=data
            )
        self._last_process_data_hash = new_hash
        disciplines_count = len(self._last_process_data)
        logger.debug(f"{self._logger_template} Парсинг OK | дисциплин: {disciplines_count}")
        return self._last_process_data

    async def detect_changes(self, old_data: dict, new_data: dict) -> List[str]:
        """Обнаружение изменений в оценках"""
        if not old_data or not new_data or old_data is new_data:
            logger.debug(f"{self._logger_template} detect_changes: нет данных для сравнения")
            return []

        old_data = {k: DisciplineMarks(**v) for k, v in old_data.items()}

        changes = []
        for discipline_name, new_discipline in new_data.items():
            old_discipline = old_data.get(discipline_name)

            if not old_discipline:
                continue

            changes.extend(self._compare_disciplines(old_discipline, new_discipline))

        logger.debug(f"{self._logger_template} detect_changes: {len(changes)} изменений")
        return changes

    def _compare_disciplines(self, old: DisciplineMarks, new: DisciplineMarks) -> list[str]:
        """Сравнение двух дисциплин"""
        hide = not self.bars_config.show_marks
        changes = []

        for i, (old_mark, new_mark) in enumerate(zip(old.marks, new.marks)):
            if old_mark.mark != new_mark.mark or old_mark.date != new_mark.date:
                new_val = f"<tg-spoiler>{new_mark.mark}</tg-spoiler>" if hide else new_mark.mark
                if not old_mark.mark:
                    changes.append(f"Оценка по {old.name} КМ-{i + 1}: {new_val}")
                else:
                    if len(old_mark.rewriting) != len(new_mark.rewriting):
                        changes.append(f"Переписывание по {old.name} КМ-{i + 1}: {old_mark.mark} -> {new_val}")
                    else:
                        changes.append(f"Изменилась оценка по {old.name} КМ-{i + 1}: {old_mark.mark} -> {new_val}")
                continue

            if len(old_mark.rewriting) != len(new_mark.rewriting):
                for rewrite in range(len(new_mark.rewriting) - len(old_mark.rewriting), 0, -1):
                    rw_val = f"<tg-spoiler>{new_mark.rewriting[-rewrite].mark}</tg-spoiler>" if hide else new_mark.rewriting[-rewrite].mark
                    changes.append(f"Переписывание по {old.name} КМ-{i + 1}: -> {rw_val}")

            for j in range(min(len(old_mark.rewriting), len(new_mark.rewriting))):
                if old_mark.rewriting[j].mark != new_mark.rewriting[j].mark:
                    rw_val = f"<tg-spoiler>{new_mark.rewriting[j].mark}</tg-spoiler>" if hide else new_mark.rewriting[j].mark
                    changes.append(f"Переписывание по {old.name} КМ-{i + 1}: {old_mark.rewriting[j].mark} -> {rw_val}")

        if old.mark_final != new.mark_final:
            final_val = f"<tg-spoiler>{new.mark_final}</tg-spoiler>" if hide else new.mark_final
            changes.append(f"Изменилась итоговая оценка по {old.name}: {old.mark_final} -> {final_val}")

        return changes

    async def close(self):
        from watchers.session.pool_session import PoolSession
        logger.debug(f"{self._logger_template} Закрытие сессии...")
        await PoolSession.release(self.credentials.user_id, "bars")
        logger.debug(f"{self._logger_template} Сессия закрыта")
