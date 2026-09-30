"""Diff снимков: оценки/пропуски (БАРС) и счётчики папок (ОСЭП).

Снимок оценок (см. ports.BarsClient.fetch_marks):
    {дисциплина: {
        "marks": [{"mark", "date", "rewriting": [{"mark", "date"}]}, ...],  # КМ-1..N
        "score": str,       # балл текущего контроля
        "mark_pa": str,     # оценка ПА («Промежуточная аттестация»)
        "mark_final": str,  # итоговая оценка
    }}

Элементы изменений оценок (MarksChanged.changes):
    {"subject", "kind", "km", "old", "new", "date"}
    kind: new_mark | mark_changed | rewriting | score_changed | final_changed |
          pa_changed;
    km — номер контрольного мероприятия (для score/final/pa отсутствует).

Снимок пропусков (fetch_absences): {ключ: {"lesson", "created", "changed"}}.
Элементы изменений (AbsencesChanged.changes):
    {"kind": "absence_new" | "absence_removed", "key", "lesson", "created", "changed",
     "date", "pair", "time_start", "time_end"}
"""

from __future__ import annotations

from src.integrations.models import SEMESTER_KEY

__all__ = [
    "diff_bars_marks",
    "diff_bars_absences",
    "apply_hidden_mark",
    "diff_folder_counts",
    "SEMESTER_KEY",
]

_MARK_KINDS = frozenset(
    {
        "new_mark",
        "mark_changed",
        "rewriting",
        "score_changed",
        "final_changed",
        "pa_changed",
    }
)


def diff_bars_marks(old: dict | None, new: dict) -> list[dict]:
    """Изменения оценок относительно снимка. Пустой снимок = базовая линия.

    Семантика (по правилу пользователя):
      * оценки не было и она появилась → new_mark;
      * оценка была и исчезла → mark_changed («5 → .»);
      * оценка на том же месте просто поменялась, ничего не добавилось
        → mark_changed;
      * добавилась новая оценка (список переписываний вырос) → rewriting;
      * изменение итоговой/ПА/балла → final_changed / pa_changed / score_changed.

    Если у дисциплины в новом снимке нет ни одной строки КМ (страница не
    разобрана) — не считаем изменением; пустые ячейки КМ при наличии строк
    считаются реальным удалением оценки.

    Правила границ семестра:
      * новые дисциплины (их не было в снимке) игнорируются — в начале
        семестра список дисциплин дополняется, уведомлять об их оценках
        не нужно;
      * сменился семестр (метка SEMESTER_KEY) — никаких уведомлений,
        новый семестр становится новой базой (совпадающие дисциплины
        не должны слать «5 → .»).
    """
    changes: list[dict] = []
    if not old:
        return changes

    old_semester = old.get(SEMESTER_KEY)
    new_semester = new.get(SEMESTER_KEY)
    if old_semester and new_semester and old_semester != new_semester:
        # сменился семестр — новый снимок становится базой без уведомлений
        return changes

    for subject, values in new.items():
        if subject == SEMESTER_KEY:
            continue
        old_values = old.get(subject)
        if old_values is None:
            continue

        old_marks = old_values.get("marks", [])
        new_marks = values.get("marks", [])
        if not new_marks:  # страница не разобрана / пустая — не изменение
            continue

        for km, (old_mark, new_mark) in enumerate(
            zip(old_marks, new_marks, strict=False), start=1
        ):
            old_value = old_mark.get("mark")
            new_value = new_mark.get("mark")
            old_rw = old_mark.get("rewriting", [])
            new_rw = new_mark.get("rewriting", [])
            grew = len(new_rw) > len(old_rw)
            value_changed = old_value != new_value or old_mark.get(
                "date"
            ) != new_mark.get("date")

            if not old_value and new_value:
                # оценки не было и она появилась
                changes.append(
                    {
                        "subject": subject,
                        "kind": "new_mark",
                        "km": km,
                        "old": None,
                        "new": new_value,
                        "date": new_mark.get("date"),
                    }
                )
            elif old_value and not new_value:
                # оценку поставили и убрали — вотчер видит «5 → .»
                changes.append(
                    {
                        "subject": subject,
                        "kind": "mark_changed",
                        "km": km,
                        "old": old_value,
                        "new": None,
                        "date": new_mark.get("date"),
                    }
                )
            elif value_changed:
                # значение на том же месте: с ростом переписываний —
                # переписывание (старая ушла в переписанные), иначе изменение
                changes.append(
                    {
                        "subject": subject,
                        "kind": "rewriting" if grew else "mark_changed",
                        "km": km,
                        "old": old_value,
                        "new": new_value,
                        "date": new_mark.get("date"),
                    }
                )
            elif grew:
                # добавились новые записи переписываний
                base = old_rw[-1]["mark"] if old_rw else old_value
                for attempt, rewrite in enumerate(new_rw[len(old_rw) :], start=1):
                    changes.append(
                        {
                            "subject": subject,
                            "kind": "rewriting",
                            "km": km,
                            "old": base,
                            "new": rewrite.get("mark"),
                            "date": rewrite.get("date"),
                            "attempt": len(old_rw) + attempt,
                            "not_counted": bool(rewrite.get("not_counted")),
                        }
                    )
            for i in range(min(len(old_rw), len(new_rw))):
                if old_rw[i].get("mark") != new_rw[i].get("mark"):
                    # запись на том же месте просто поменялась
                    changes.append(
                        {
                            "subject": subject,
                            "kind": "mark_changed",
                            "km": km,
                            "old": old_rw[i].get("mark"),
                            "new": new_rw[i].get("mark"),
                            "date": new_rw[i].get("date"),
                            "attempt": i + 1,
                            "not_counted": bool(new_rw[i].get("not_counted")),
                        }
                    )

        if (old_values.get("score") or "") != (values.get("score") or ""):
            changes.append(
                {
                    "subject": subject,
                    "kind": "score_changed",
                    "km": None,
                    "old": old_values.get("score") or None,
                    "new": values.get("score") or None,
                    "date": None,
                }
            )
        if (old_values.get("mark_final") or "") != (values.get("mark_final") or ""):
            changes.append(
                {
                    "subject": subject,
                    "kind": "final_changed",
                    "km": None,
                    "old": old_values.get("mark_final") or None,
                    "new": values.get("mark_final") or None,
                    "date": None,
                }
            )
        if (old_values.get("mark_pa") or "") != (values.get("mark_pa") or ""):
            changes.append(
                {
                    "subject": subject,
                    "kind": "pa_changed",
                    "km": None,
                    "old": old_values.get("mark_pa") or None,
                    "new": values.get("mark_pa") or None,
                    "date": None,
                }
            )
    return changes


def diff_bars_absences(old: dict | None, new: dict) -> list[dict]:
    """Изменения пропусков: новые и исчезнувшие записи.

    При смене семестра (метка SEMESTER_KEY) — без уведомлений: старые
    пропуски не должны считаться «снятыми».
    """
    changes: list[dict] = []
    if not old:
        return changes

    old_semester = old.get(SEMESTER_KEY)
    new_semester = new.get(SEMESTER_KEY)
    if old_semester and new_semester and old_semester != new_semester:
        return changes

    for key, values in new.items():
        if key == SEMESTER_KEY:
            continue
        if key not in old:
            changes.append({"kind": "absence_new", "key": key, **values})
    for key, values in old.items():
        if key == SEMESTER_KEY:
            continue
        if key not in new:
            changes.append({"kind": "absence_removed", "key": key, **values})
    return changes


def apply_hidden_mark(changes: list[dict]) -> list[dict]:
    """Скрыть значения оценок (настройка «скрывать оценку»).

    Маскирует только изменения оценок; пропуски не трогает.
    """
    masked = []
    for change in changes:
        item = dict(change)
        if item.get("kind") in _MARK_KINDS:
            item["old"] = None
            item["new"] = None
            item["hidden"] = True
        masked.append(item)
    return masked


def diff_folder_counts(
    old: dict | None, new: dict, ignore: frozenset[str] = frozenset()
) -> dict[str, int]:
    """Папки, где выросло количество писем: {папка: сколько прибавилось}."""
    if not old:
        return {}
    grown: dict[str, int] = {}
    for folder, count in new.items():
        if folder in ignore:
            continue
        old_count = old.get(folder)
        if old_count is not None and count > old_count:
            grown[folder] = count - old_count
    return grown
