"""Профиль БАРС (ФИО/группа/ответственный): сохранение и свежесть.

Профиль может меняться (перевод, смена группы, смена ответственного) —
поэтому он перепроверяется:
  - при каждом успешном входе в БАРС (включая включение вотчера пользователем
    и первый вход после запуска процесса);
  - раз в сутки во время обычных проверок.

save_bars_profile сравнивает новое со старым и пишет в лог только изменения.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from loguru import logger

__all__ = ["PROCESS_STARTED_AT", "profile_stale", "save_bars_profile"]

log = logger.bind(component="checks")

# момент запуска процесса: профиль, сохранённый ДО запуска, считается устаревшим
# — после перезапуска бота/worker'а профиль перепроверяется при первой же удачной
# авторизации, не дожидаясь суточного TTL
PROCESS_STARTED_AT = datetime.now(UTC)


def profile_stale(row, *, max_age: timedelta = timedelta(hours=24)) -> bool:
    """Профиль пора перепроверить: записи нет, данные старше суток или
    сохранены до запуска этого процесса."""
    if row is None:
        return True
    updated = getattr(row, "updated_at", None)
    if updated is None:
        return True
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    return updated < PROCESS_STARTED_AT or datetime.now(UTC) - updated > max_age


async def save_bars_profile(uow, user_id: int, profile) -> None:
    """Сохранить профиль БАРС; изменения полей пишутся в лог."""
    from src.db.models import BarsProfile

    row = await uow.bars_profile.get_by_user_id(user_id)
    if row is None:
        row = BarsProfile(user_id=user_id)
        await uow.bars_profile.add(row)
        changes = ["создана запись"]
    else:
        changes = []
        for field, title in (
            ("fio", "ФИО"),
            ("group", "группа"),
            ("is_journal_responsible", "ответственный"),
        ):
            old, new = getattr(row, field, None), getattr(profile, field, None)
            if field == "is_journal_responsible":
                new = bool(new)
            if old != new:
                changes.append(f"{title}: {old or '—'} → {new or '—'}")
        row.fio = profile.fio
        row.group = profile.group
        row.is_journal_responsible = bool(profile.is_journal_responsible)
    if changes:
        log.info("профиль БАРС обновлён | user={} | {}", user_id, "; ".join(changes))
