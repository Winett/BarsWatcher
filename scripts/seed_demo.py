"""Демо-сид: глобальные настройки + пользователь с включённым отслеживанием.

После запуска worker опрашивает этого пользователя (sim-интеграции) и гоняет
события через очередь — удобно увидеть, что ядро живо.

Запуск: uv run python scripts/seed_demo.py
"""

import os
import sys
from datetime import UTC, datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select

from src.db.database import session_factory
from src.db.models import (
    BarsCredentials,
    BarsProfile,
    BarsSettings,
    BarsState,
    GlobalSettings,
    OsepCredentials,
    OsepSettings,
    OsepState,
    User,
    WatchStates,
)

DEMO_TELEGRAM_ID = 1


async def main() -> None:
    async with session_factory() as session:
        exists = (
            await session.execute(
                select(User).where(User.telegram_id == DEMO_TELEGRAM_ID)
            )
        ).scalar_one_or_none()
        if exists is not None:
            print(f"демо-пользователь уже есть (id={exists.id})")
            return

        global_row = (
            await session.execute(select(GlobalSettings).limit(1))
        ).scalars().first()
        if global_row is None:
            session.add(GlobalSettings())  # дефолты моделей

        user = User(telegram_id=DEMO_TELEGRAM_ID, username="demo")
        session.add(user)
        await session.flush()

        now = datetime.now(UTC)

        session.add(BarsCredentials(user_id=user.id, login="demo", password="demo"))
        session.add(
            BarsProfile(
                user_id=user.id,
                fio="Демо Пользователь",
                group="ИВТ-401",
                is_journal_responsible=True,
            )
        )
        session.add(
            BarsState(user_id=user.id, status=WatchStates.ACTIVE, next_check=now)
        )
        session.add(BarsSettings(user_id=user.id))

        session.add(OsepCredentials(user_id=user.id, login="demo", password="demo"))
        session.add(
            OsepState(user_id=user.id, status=WatchStates.ACTIVE, next_check=now)
        )
        session.add(OsepSettings(user_id=user.id, blacklist=[]))

        await session.commit()
        print(f"демо-пользователь создан (id={user.id}), отслеживание БАРС+ОСЭП включено")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
