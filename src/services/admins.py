"""Администраторы: импорт из .env, проверки, уведомления админам.

Админов получаем по **telegram_id** (переменная окружения ADMINS — список
telegram_id). При первом запуске (таблица admins пуста) они импортируются
вместе с User-записями; дальше источник правды — БД, все изменения делаются
из админки и переживают перезапуски.
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy import delete, func, select

from src.core.config import settings
from src.db.database import session_factory
from src.db.models import Admin, User

__all__ = [
    "sync_admins_from_settings",
    "sync_global_settings_from_env",
    "is_admin_user",
    "is_env_admin",
    "admin_telegram_ids",
    "add_admin",
    "remove_admin",
    "admin_count",
    "notify_admins",
    "deactivate_user",
]

log = logger.bind(component="admins")

# глобальные настройки, которые импортируются из .env при первом запуске
_GLOBAL_FIELDS = (
    "poll_interval_seconds",
    "max_poll_interval_seconds",
    "request_timeout_seconds",
    "error_timeout_seconds",
    "auto_scale_enabled",
    "notify_service_status",
    "notify_new_users",
)


def is_env_admin(telegram_id: int) -> bool:
    """Администратор задан в .env (ADMINS) — его нельзя удалить из админки."""
    return telegram_id in settings.admins


async def sync_global_settings_from_env() -> bool:
    """Импорт глобальных настроек из .env — только при пустой таблице (первый запуск).

    Дальше источник правды — БД: изменения из админки переживают перезапуски.
    Возвращает True, если настройки были импортированы.
    """
    from src.repositories.uow import UnitOfWork

    async with session_factory() as session:
        uow = UnitOfWork(session)
        if await uow.config.get_global() is not None:
            return False
        row = await uow.config.get_or_create_global()
        for field in _GLOBAL_FIELDS:
            setattr(row, field, getattr(settings, field))
        await uow.commit()
        log.info("глобальные настройки импортированы из .env")
        return True


async def sync_admins_from_settings() -> int:
    """Гарантия присутствия админов из ADMINS (.env) при каждом старте.

    Добавляет недостающих (вместе с User-записями): даже если записи пропали —
    например, пользователя удалили вручную из БД вместе с каскадом admins, —
    env-администраторы восстановятся. Возвращает число добавленных.
    """
    added = 0
    async with session_factory() as session:
        for telegram_id in settings.admins:
            user = (
                await session.execute(
                    select(User).where(User.telegram_id == telegram_id)
                )
            ).scalars().first()
            if user is None:
                user = User(telegram_id=telegram_id, is_active=True)
                session.add(user)
                await session.flush()
                log.info("восстановлен пользователь админа {}", telegram_id)
            exists = (
                await session.execute(
                    select(Admin.id).where(Admin.user_id == user.id)
                )
            ).first()
            if exists:
                continue
            session.add(Admin(user_id=user.id))
            added += 1
            log.info("добавлен администратор {} (из ADMINS .env)", telegram_id)
        await session.commit()
    return added


async def is_admin_user(telegram_id: int) -> bool:
    async with session_factory() as session:
        stmt = (
            select(Admin.id)
            .join(User, User.id == Admin.user_id)
            .where(User.telegram_id == telegram_id)
            .limit(1)
        )
        return (await session.execute(stmt)).first() is not None


async def admin_count() -> int:
    async with session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(Admin)) or 0)


async def admin_telegram_ids() -> list[int]:
    async with session_factory() as session:
        rows = await session.execute(
            select(User.telegram_id).join(Admin, Admin.user_id == User.id)
        )
        return [row[0] for row in rows]


async def add_admin(telegram_id: int, username: str | None = None) -> tuple[bool, str]:
    """Добавить админа по telegram_id (User создаётся при необходимости)."""
    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == telegram_id))
        ).scalars().first()
        if user is None:
            user = User(telegram_id=telegram_id, username=username, is_active=True)
            session.add(user)
            await session.flush()
        exists = (
            await session.execute(select(Admin.id).where(Admin.user_id == user.id))
        ).first()
        if exists:
            return False, f"{telegram_id} уже администратор"
        session.add(Admin(user_id=user.id))
        await session.commit()
        return True, f"{telegram_id} добавлен в администраторы"


async def remove_admin(telegram_id: int) -> tuple[bool, str]:
    """Убрать админа по telegram_id (env-админов и последнего убрать нельзя)."""
    if is_env_admin(telegram_id):
        return False, "администратор задан в .env (ADMINS) — удалить нельзя"
    if await admin_count() <= 1:
        return False, "нельзя удалить последнего администратора"
    async with session_factory() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == telegram_id))
        ).scalars().first()
        if user is None:
            return False, f"пользователь {telegram_id} не найден"
        result = await session.execute(delete(Admin).where(Admin.user_id == user.id))
        await session.commit()
        if result.rowcount == 0:
            return False, f"{telegram_id} не является администратором"
        return True, f"{telegram_id} больше не администратор"


async def deactivate_user(telegram_id: int) -> None:
    """Пользователь заблокировал бота: is_active=False, вотчеры выключаются.

    Вызывается, когда Telegram отвечает Forbidden (бот заблокирован) — при
    уведомлении или рассылке. Рассылка неактивным не делается.
    """
    from src.db.models import WatchStates
    from src.repositories.uow import UnitOfWork

    async with session_factory() as session:
        uow = UnitOfWork(session)
        user = await uow.users.get_by_telegram_id(telegram_id)
        if user is None:
            return
        user.is_active = False
        turned_off = 0
        for state in (
            await uow.bars_state.get_by_user_id(user.id),
            await uow.osep_state.get_by_user_id(user.id),
        ):
            if state is not None and state.status == WatchStates.ACTIVE:
                state.status = WatchStates.INACTIVE
                turned_off += 1
        await uow.commit()
        log.info(
            "пользователь {} деактивирован (бот заблокирован) | вотчеров выключено: {}",
            telegram_id,
            turned_off,
        )


async def notify_admins(bot, text: str) -> None:
    """Разослать сообщение всем админам (best-effort, падение не роняет бота)."""
    ids = await admin_telegram_ids()
    if not ids:
        log.warning("уведомление админам: некому отправлять (админов нет) | {}", text)
        return
    for telegram_id in ids:
        try:
            await bot.send_message(telegram_id, text)
        except Exception as error:
            log.error("не удалось уведомить админа {}: {}", telegram_id, error)
    log.info("уведомление админам ({} шт.) | {}", len(ids), text)
