"""Миграция данных с master-ветки (старая БД) на rewrite-ветку (новая БД).

Старая схема (master, одна таблица users + global_config/user_config):
    users: user_id (telegram), username, used_bars/used_osep,
           bars_login/bars_password, osep_login/osep_password — пароли
           хранились ОТКРЫТЫМ текстом (шифрования в master не было).
    global_config: poll_interval, max_poll_interval, timeout, auto_scale_enabled.
    user_config: user_id, poll_interval, auto_scale_enabled,
                 bars_show_marks, osep_blacklist.

Новая схема (rewrite): users + bars_credentials/osep_credentials (пароли
шифруются EncryptedString/SecurityManager), bars_states/osep_states
(status=ACTIVE если мониторинг был включён), bars_settings/osep_settings,
global_settings/user_overrides.

Использование:
    # 1. новая БД поднята и мигрирована: alembic upgrade head
    # 2. обе БД доступны одновременно (старая — например на 5432, новая на 5433)
    uv run python scripts/migrate_from_master.py \
        --old-db-url postgresql+asyncpg://user:pass@host:5432/old_db \
        --new-db-url postgresql+asyncpg://user:pass@host:5433/new_db

БЕЗОПАСНОСТЬ: скрипт НЕ трогает старую БД (только чтение). Повторный запуск
пропускает уже перенесённых пользователей (по telegram_id).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.core.config import settings as app_settings  # noqa: E402
from src.core.security import SecurityManager  # noqa: E402
from src.db.models import (  # noqa: E402
    BarsCredentials,
    BarsSettings,
    BarsState,
    GlobalSettings,
    OsepCredentials,
    OsepSettings,
    OsepState,
    User,
    UserOverride,
    WatchStates,
)


def _split_blacklist(raw: str | None) -> list[str]:
    """Строка-чёрный список из master → список паттернов."""
    if not raw:
        return []
    parts = [p.strip() for p in raw.replace("\n", ",").split(",")]
    return [p for p in parts if p]


async def _fetch_old(engine, query) -> list:
    async with engine.connect() as conn:
        result = await conn.execute(query)
        return [dict(row._mapping) for row in result]


async def migrate(old_url: str, new_url: str) -> None:
    from sqlalchemy import text

    old_engine = create_async_engine(old_url)
    new_engine = create_async_engine(new_url)
    new_session = async_sessionmaker(new_engine, class_=AsyncSession, expire_on_commit=False)
    crypto = SecurityManager(app_settings.secret_key.get_secret_value())

    # ── глобальные настройки ──
    old_globals = await _fetch_old(old_engine, text("SELECT * FROM global_config LIMIT 1"))
    async with new_session() as session:
        existing = (await session.execute(select(GlobalSettings))).scalars().first()
        if old_globals:
            g = old_globals[0]
            if existing is None:
                session.add(
                    GlobalSettings(
                        poll_interval_seconds=g.get("poll_interval") or 180,
                        max_poll_interval_seconds=g.get("max_poll_interval") or 1800,
                        request_timeout_seconds=g.get("timeout") or 30,
                        auto_scale_enabled=bool(g.get("auto_scale_enabled", True)),
                    )
                )
            else:
                existing.poll_interval_seconds = g.get("poll_interval") or 180
                existing.max_poll_interval_seconds = g.get("max_poll_interval") or 1800
                existing.request_timeout_seconds = g.get("timeout") or 30
                existing.auto_scale_enabled = bool(g.get("auto_scale_enabled", True))
            await session.commit()
            print("global_settings: перенесены")

    # ── пользователи ──
    old_users = await _fetch_old(old_engine, text("SELECT * FROM users"))
    old_configs = {
        row["user_id"]: row
        for row in await _fetch_old(old_engine, text("SELECT * FROM user_config"))
    }

    migrated = skipped = 0
    async with new_session() as session:
        for old in old_users:
            tg_id = int(old["user_id"])
            exists = (
                await session.execute(select(User).where(User.telegram_id == tg_id))
            ).scalars().first()
            if exists is not None:
                skipped += 1
                continue

            user = User(
                telegram_id=tg_id,
                username=old.get("username"),
                is_active=True,
                is_blocked=False,
            )
            session.add(user)
            await session.flush()  # получаем user.id

            # креды БАРС (пароли в master — открытым текстом, шифруем при переносе)
            if old.get("bars_login") or old.get("bars_password"):
                session.add(
                    BarsCredentials(
                        user_id=user.id,
                        login=old.get("bars_login"),
                        password=(
                            crypto.encrypt(old["bars_password"])
                            if old.get("bars_password")
                            else None
                        ),
                    )
                )
            if old.get("osep_login") or old.get("osep_password"):
                session.add(
                    OsepCredentials(
                        user_id=user.id,
                        login=old.get("osep_login"),
                        password=(
                            crypto.encrypt(old["osep_password"])
                            if old.get("osep_password")
                            else None
                        ),
                    )
                )

            # состояние мониторинга: used_bars/used_osep → ACTIVE
            session.add(
                BarsState(
                    user_id=user.id,
                    status=WatchStates.ACTIVE if old.get("used_bars") else WatchStates.INITIAL,
                )
            )
            session.add(
                OsepState(
                    user_id=user.id,
                    status=WatchStates.ACTIVE if old.get("used_osep") else WatchStates.INITIAL,
                )
            )

            # персональные настройки
            cfg = old_configs.get(tg_id)
            if cfg is not None:
                if cfg.get("poll_interval") is not None or cfg.get("auto_scale_enabled") is not None:
                    session.add(
                        UserOverride(
                            user_id=user.id,
                            poll_interval_seconds=cfg.get("poll_interval"),
                            auto_scale_enabled=cfg.get("auto_scale_enabled"),
                        )
                    )
                if cfg.get("bars_show_marks") is not None:
                    # master: bars_show_marks=True — показывать; hidden_mark — инверсия
                    session.add(BarsSettings(user_id=user.id, hidden_mark=not cfg["bars_show_marks"]))
                if cfg.get("osep_blacklist"):
                    session.add(
                        OsepSettings(
                            user_id=user.id,
                            blacklist=_split_blacklist(cfg["osep_blacklist"]),
                        )
                    )
            migrated += 1

        await session.commit()

    print(f"пользователей перенесено: {migrated}, пропущено (уже есть): {skipped}")

    await old_engine.dispose()
    await new_engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Миграция данных с master-БД на rewrite-БД"
    )
    parser.add_argument(
        "--old-db-url",
        required=True,
        help="URL старой БД (master), например postgresql+asyncpg://user:pass@host:5432/old_db",
    )
    parser.add_argument(
        "--new-db-url",
        required=True,
        help="URL новой БД (rewrite), например postgresql+asyncpg://user:pass@host:5433/new_db",
    )
    args = parser.parse_args()
    asyncio.run(migrate(args.old_db_url, args.new_db_url))
