from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

from filters.admin import AdminFilter
from database.db import async_session
from database.services.config_service import ConfigService

router = Router(name=__name__)


@router.message(AdminFilter(), Command('test_bars'))
async def test_bars_notification(msg: Message):
    """Тестовое уведомление БАРС — имитация изменения оценки."""
    cs = ConfigService(async_session)
    bars_cfg = await cs.resolve_bars_config(msg.from_user.id)

    hide = not bars_cfg.show_marks
    m1 = f"<tg-spoiler>5</tg-spoiler>" if hide else "5"
    m2 = f"<tg-spoiler>4</tg-spoiler>" if hide else "4"
    m3 = f"<tg-spoiler>5</tg-spoiler>" if hide else "5"

    test_message = (
        "🔔 <b>Тестовое уведомление БАРС</b>\n\n"
        f"Оценка по Математика КМ-1: {m1}\n"
        f"Переписывание по Информатика КМ-2: 3 -> {m2}\n"
        f"Изменилась итоговая оценка по Физика: 4 -> {m3}"
    )
    await msg.answer(test_message, parse_mode="HTML")


@router.message(AdminFilter(), Command('test_osep'))
async def test_osep_notification(msg: Message):
    """Тестовое уведомление ОСЭП — имитация нового письма."""
    test_message = (
        "📧 <b>Тестовое уведомление ОСЭП</b>\n\n"
        "Новое письмо от: <b>Иванов Иван Иванович</b>\n"
        "Тема: <b>Зачёт по дисциплине</b>"
    )
    await msg.answer(test_message, parse_mode="HTML")
