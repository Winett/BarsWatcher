"""Слияние настроек опроса: персональное поле перекрывает глобальное,
не указанное поле берётся из глобального (по-полевое, не пакетами)."""

from __future__ import annotations

from dataclasses import dataclass, fields

from sqlalchemy.ext.asyncio import AsyncSession

from src.repositories.config import ConfigRepository

__all__ = ["PollSettings", "DEFAULT_POLL_SETTINGS", "merge_settings", "resolve_poll_settings"]


@dataclass(frozen=True)
class PollSettings:
    poll_interval_seconds: int
    max_poll_interval_seconds: int
    request_timeout_seconds: int
    error_timeout_seconds: int
    auto_scale_enabled: bool


DEFAULT_POLL_SETTINGS = PollSettings(
    poll_interval_seconds=10,
    max_poll_interval_seconds=1800,
    request_timeout_seconds=30,
    error_timeout_seconds=300,
    auto_scale_enabled=True,
)

_FIELD_NAMES = tuple(f.name for f in fields(PollSettings))


def merge_settings(global_row, override_row) -> PollSettings:
    values = {name: getattr(DEFAULT_POLL_SETTINGS, name) for name in _FIELD_NAMES}
    for row in (global_row, override_row):
        if row is None:
            continue
        for name in _FIELD_NAMES:
            value = getattr(row, name, None)
            if value is not None:
                values[name] = value
    return PollSettings(**values)


async def resolve_poll_settings(session: AsyncSession, user_id: int) -> PollSettings:
    repo = ConfigRepository(session)
    global_row = await repo.get_global()
    override_row = await repo.get_override(user_id)
    return merge_settings(global_row, override_row)
