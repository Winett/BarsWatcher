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