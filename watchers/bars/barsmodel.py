from pydantic import BaseModel, Field
from typing import List, Optional

class DisciplineWatcher(BaseModel):
    discipline: str | None = None
    marks: List[int] = Field(default_factory=list)
    mark_PA: Optional[str] = ''
    mark_final: Optional[str] = ''

    def find_changes(self, other: 'DisciplineWatcher'):
        changes = []
        if self.discipline != other.discipline:
            return False

        for i, (old_mark, new_mark) in enumerate(zip(self.marks, other.marks)):
            if old_mark != new_mark:
                changes.append(f"Изменение в {self.discipline}: КМ-{i + 1}: {old_mark} → {new_mark}")

        if self.mark_PA != other.mark_PA:
            changes.append(f"Изменение в {self.discipline}: ПА: {self.mark_PA} → {other.mark_PA}")

        if self.mark_final != other.mark_final:
            changes.append(f"Изменение в {self.discipline}: Итоговая оценка: {self.mark_final} → {other.mark_final}")

        if len(self.marks) != len(other.marks):
            changes.append(f"Изменение в {self.discipline}: Количество КМ изменилось: {len(self.marks)} → {len(other.marks)}")

        return changes


class DisciplineSkip(BaseModel):
    discipline: str | None = None
    lessons_in_journal: int = 0
    skips: int = 0
    skip_for_good_reasons: int = 0
    skip_without_reason_percent: float = 0
    lessons_in_shedule: int = 0
    skip_without_reason_in_shedule_percent: float = 0


    def find_changes(self, other: 'DisciplineSkip'):
        changes = []
        if self.discipline != other.discipline:
            return False

        if self.skips != other.skips:
            changes.append(f"Изменение в {self.discipline}: Количество пропусков изменилось: {self.skips} → {other.skips} ({self.skip_without_reason_in_shedule_percent}%)")

        return changes