"""Уведомления в Telegram по событиям (очередь bot).

Лимит Telegram — 4096 символов на сообщение: длинные письма разбиваются
на части (с запасом), файлы отправляются отдельным сообщением с ответом
на первое сообщение письма. Код 2FA вводится по кнопке (пользователь сам
решает, когда продолжить).
"""

from __future__ import annotations

from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import FSInputFile, InputMediaDocument
from loguru import logger

from src.bot.throttle import send_with_retry
from src.db.database import session_factory
from src.db.models import ServiceKind
from src.events.bus import subscribe
from src.events.contracts import (
    AbsencesChanged,
    Auth2FACodeResent,
    Auth2FAFailed,
    Auth2FARequired,
    AuthCompleted,
    AuthFailed,
    Event,
    LessonsCheckResult,
    MailReceived,
    MarksChanged,
    ReminderDue,
    ServiceStatusChanged,
)
from src.integrations import get_osep_client
from src.repositories.uow import UnitOfWork
from src.services.admins import deactivate_user, notify_admins
from src.services.attachments import AttachmentCache
from src.services.lessons import (
    LessonIssue,
    describe_issue,
    describe_missing,
)

from .keyboards import two_fa_prompt_kb

__all__ = ["register_telegram_notifier", "split_message", "TG_LIMIT"]

TG_LIMIT = 4096
CHUNK = 4000  # с запасом под лимит Telegram
MEDIA_GROUP_LIMIT = 10  # Telegram: send_media_group принимает до 10 медиа

_cache = AttachmentCache()
_bot: Bot | None = None


def split_message(text: str, limit: int = CHUNK) -> list[str]:
    """Разбить текст на части <= limit, не разрывая строки по возможности."""
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > limit and current:
            parts.append(current)
            current = ""
        while len(line) > limit:  # сверхдлинная строка
            parts.append(line[:limit])
            line = line[limit:]
        current += line
    if current:
        parts.append(current)
    return parts or [""]


# Заголовок новых оценок — прежний формат уведомления (как в старом приложении)
_NEW_MARK_TITLE = "📝 Новая оценка"

_MARK_KINDS_TEXT = {
    "mark_changed": "📘 Оценка изменена",
    "rewriting": "🔁 Переписывание",
    "final_changed": "🏆 Итоговая оценка",
    "pa_changed": "📊 Промежуточная аттестация",
}


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return "—"
    parts = iso.split("-")
    if len(parts) != 3:
        return iso
    return f"{parts[2]}.{parts[1]}.{parts[0][2:]}"


def _value(value: str | None, hidden: bool) -> str:
    text = escape(value) if value else "."
    return f"<tg-spoiler>{text}</tg-spoiler>" if hidden else text


def _render_new_mark(change: dict) -> str:
    """Новая оценка — прежний формат: «Оценка по <дисциплина> КМ-N: 5»."""
    subject = escape(str(change.get("subject", "?")))
    km = change.get("km")
    where = f"{subject} КМ-{km}" if km else subject
    hidden = bool(change.get("hidden"))
    return f"Оценка по {where}: {_value(change.get('new'), hidden)}"


def _render_mark_change(change: dict) -> str:
    kind = change.get("kind")
    title = _MARK_KINDS_TEXT.get(kind, "Изменение оценки")
    subject = escape(str(change.get("subject", "?")))
    km = change.get("km")
    where = f"«{subject}»" + (f", КМ-{km}" if km else "")
    hidden = bool(change.get("hidden"))
    old, new = change.get("old"), change.get("new")

    if kind in {"final_changed", "pa_changed"}:
        return (
            f"{title}: «{subject}» — "
            f"{_value(old, hidden)} → {_value(new, hidden)}"
        )
    date_part = f" ({_fmt_date(change.get('date'))})" if new else ""
    return f"{title}: {where} — {_value(old, hidden)} → {_value(new, hidden)}{date_part}"


def _render_marks(changes: list[dict]) -> str | None:
    """Сообщение об изменениях оценок.

    Новые оценки — прежним форматом (заголовок «📝 Новая оценка», пустая
    строка, «Оценка по … КМ-N: 5»). «Балл текущего контроля» не показываем
    вовсе: это производная величина, шумящая сама по себе.
    """
    # score_changed может остаться только в старых событиях, застрявших в
    # очереди Redis до обновления — такие строки молча пропускаем.
    new_marks = [c for c in changes if c.get("kind") == "new_mark"]
    others = [
        c
        for c in changes
        if c.get("kind") not in {"new_mark", "score_changed"}
    ]
    parts = []
    if new_marks:
        lines = "\n".join(_render_new_mark(c) for c in new_marks)
        parts.append(f"{_NEW_MARK_TITLE}\n\n{lines}")
    if others:
        parts.append("\n".join(_render_mark_change(c) for c in others))
    return "\n\n".join(parts) or None


def _format_event_html(event: Event) -> str | None:
    if isinstance(event, MarksChanged):
        return _render_marks(event.changes)
    if isinstance(event, AbsencesChanged):
        lines = []
        for change in event.changes:
            title = (
                "✅ Пропуск снят"
                if change.get("kind") == "absence_removed"
                else "🚶 Новый пропуск"
            )
            lines.append(f"{title}: {escape(str(change.get('lesson', '?')))}")
        return "\n".join(lines) or None
    if isinstance(event, Auth2FARequired):
        title = _service_title(event.service)
        destination = escape(event.destination or "куда — неизвестно")
        return (
            f"🔐 {title}: требуется код 2FA.\n"
            f"Код отправлен: {destination}\n"
            "Когда будете готовы — нажмите «Ввести код 2FA»."
        )
    if isinstance(event, Auth2FACodeResent):
        title = _service_title(event.service)
        destination = escape(event.destination or "куда — неизвестно")
        return f"🔁 {title}: код 2FA отправлен повторно ({destination})"
    if isinstance(event, Auth2FAFailed):
        return f"❌ {_service_title(event.service)}: код 2FA введён неверно"
    if isinstance(event, AuthCompleted):
        return (
            f"✅ {_service_title(event.service)}: авторизация завершена — "
            "отслеживание включено"
        )
    if isinstance(event, AuthFailed):
        reason = escape(event.reason or "причина неизвестна")
        return f"❌ {_service_title(event.service)}: авторизация не удалась — {reason}"
    if isinstance(event, ReminderDue):
        lines = ["⏰ Напоминание: проверьте, проставлены ли пары"]
        issues = [LessonIssue.from_dict(item) for item in (event.issues or [])]
        if issues:
            lines.append("")
            issue_lines = []
            for issue in issues:
                if issue.kind == "missing":
                    issue_lines.append(describe_missing(issue))
                else:
                    issue_lines.append(describe_issue(issue))
            lines.append("\n".join(issue_lines))
        return "\n\n".join(lines)
    if isinstance(event, LessonsCheckResult):
        if event.error:
            return f"🔍 Сверка пар: не получилось — {escape(event.error)}"
        issues = [LessonIssue.from_dict(item) for item in (event.issues or [])]
        if event.days == 1:
            # проверка за сегодня (как напоминание в 18:00) — полная картина
            header = "📋 Проверка пар за сегодня"
            empty = "✅ Все пары проставлены и в порядке"
        else:
            # сверка с начала семестра: показываем только непроставленные пары
            header = f"🔍 Сверка пар с расписанием ({escape(event.period)})"
            issues = [issue for issue in issues if issue.kind == "missing"]
            empty = "✅ Непроставленных пар нет"
        lines = [header]
        if not issues:
            lines.append(empty)
        else:
            lines.append("")
            issue_lines = []
            for issue in issues:
                if issue.kind == "missing":
                    issue_lines.append(describe_missing(issue))
                else:
                    issue_lines.append(describe_issue(issue))
            lines.append("\n".join(issue_lines))
        return "\n\n".join(lines)
    return None


def _service_title(service: ServiceKind) -> str:
    return {ServiceKind.BARS: "БАРС", ServiceKind.OSEP: "ОСЭП"}.get(service, str(service))


async def _telegram_id(user_id: int) -> int | None:
    async with session_factory() as session:
        uow = UnitOfWork(session)
        user = await uow.users.get_by_id(user_id)
        return user.telegram_id if user else None


async def _user_login(user_id: int) -> str | None:
    """Логин БАРС пользователя для логов (или None)."""
    async with session_factory() as session:
        uow = UnitOfWork(session)
        creds = await uow.bars_credentials.get_by_user_id(user_id)
        return creds.login if creds else None


async def _send(chat_id: int, text: str, reply_to: int | None = None) -> int | None:
    """Отправить текст (частями); вернуть id первого сообщения."""
    assert _bot is not None
    first_id: int | None = None
    for part in split_message(text):
        try:
            # ≤20 сообщений/с + повтор при 429 (TelegramRetryAfter)
            message = await send_with_retry(
                lambda p=part, r=reply_to: _bot.send_message(
                    chat_id, p, reply_to_message_id=r
                )
            )
        except TelegramForbiddenError:
            # пользователь заблокировал бота — деактивируем и выключаем вотчеры
            await deactivate_user(chat_id)
            return None
        if first_id is None:
            first_id = message.message_id
        reply_to = None  # части цепляются только к первому сообщению
    return first_id


async def _on_marks(event: MarksChanged) -> None:
    text = _format_event_html(event)
    if text:
        await _notify(event.user_id, text)


async def _on_absences(event: AbsencesChanged) -> None:
    text = _format_event_html(event)
    if text:
        await _notify(event.user_id, text)


async def _on_auth(event: Event) -> None:
    text = _format_event_html(event)
    if text:
        await _notify(event.user_id, text)


async def _on_reminder(event: ReminderDue) -> None:
    text = _format_event_html(event)
    if text:
        await _notify(event.user_id, text)


async def _on_2fa_required(event: Auth2FARequired) -> None:
    text = _format_event_html(event)
    chat_id = await _telegram_id(event.user_id)
    if text and chat_id:
        logger.info("отправка → user={} | 2FA запрос кода ({})", event.user_id, event.service)
        await _bot.send_message(
            chat_id,
            text,
            reply_markup=two_fa_prompt_kb(str(event.service)),
        )


async def _on_mail(event: MailReceived) -> None:
    chat_id = await _telegram_id(event.user_id)
    if chat_id is None:
        logger.warning("письмо: нет пользователя id={}", event.user_id)
        return
    mail = event.mail or {}
    sender = mail.get("sender_name") or mail.get("sender_email") or "?"
    subject = str(mail.get("subject", "?"))
    attachments = mail.get("attachments") or []
    logger.info(
        "отправка → user={} tg={} | письмо «{}» от {} | вложений: {}",
        event.user_id,
        chat_id,
        subject,
        sender,
        len(attachments),
    )
    header = (
        f"📧 Новое письмо (папка «{escape(str(event.folder))}»)!\n"
        f"Тема: {escape(str(mail.get('subject', '?')))}\n"
        f"От: {escape(str(sender))}\n\n"
    )
    body = escape(str(mail.get("body") or ""))
    full = header + body
    if attachments:
        full += "\n\nЕсть вложения!"
    first_id = await _send(chat_id, full)

    attachments = mail.get("attachments") or []
    if not attachments or first_id is None:
        return
    await _send_attachments(event.user_id, chat_id, first_id, attachments)


async def _restore_osep_session(user_id: int, client) -> None:
    """Восстановить сессию ОСЭП из БД (fallback-скачивание в боте)."""
    try:
        async with session_factory() as session:
            uow = UnitOfWork(session)
            obj = await uow.osep_state.get_by_user_id(user_id)
            cookies = obj.cookies if obj else None
        if cookies:
            client.restore_session({"cookies": cookies, "pending_2fa": None})
    except Exception as error:
        logger.error("не удалось восстановить сессию ОСЭП | user={}: {}", user_id, error)


async def _send_attachments(
    user_id: int, chat_id: int, reply_to: int, attachments: list[dict]
) -> None:
    """Файлы одним сообщением-альбомом (send_media_group, ≤10 файлов на альбом).

    Если worker уже положил файл в кэш — используем готовый путь, НЕ скачиваем
    повторно (иначе двойное скачивание и двойное удаление в логах).
    """
    client = get_osep_client(user_id)
    await _restore_osep_session(user_id, client)
    paths: list[tuple[str, str]] = []  # (путь, имя файла) — последовательность скачанных
    for attachment in attachments:
        attachment_id = str(attachment.get("attachment_id") or "")
        filename = str(attachment.get("filename") or "file")
        size = int(attachment.get("size") or 0)
        cached = _cache.get_if_cached(
            attachment_id=attachment_id or filename,
            filename=filename,
            size=size,
            user_id=user_id,
        )
        if cached is not None:
            paths.append((cached, filename))
            continue
        try:
            path = await _cache.get_or_download(
                attachment_id=attachment_id or filename,
                filename=filename,
                size=size,
                user_id=user_id,
                download=lambda a=attachment_id: client.download_attachment(a),
            )
        except Exception as error:
            logger.error("вложение {} не скачалось: {}", filename, error)
            continue
        paths.append((path, filename))

    if not paths:
        await _send(chat_id, "⚠️ Не удалось отправить вложения", reply_to)
        return

    # отправляем альбомами по ≤10 файлов (лимит Telegram на send_media_group)
    for start in range(0, len(paths), MEDIA_GROUP_LIMIT):
        batch = paths[start : start + MEDIA_GROUP_LIMIT]
        media = [
            InputMediaDocument(media=FSInputFile(p, filename=f)) for p, f in batch
        ]
        try:
            # первый альбом отвечает на первое сообщение письма, остальные — нет
            await send_with_retry(
                lambda m=media, r=(reply_to if start == 0 else None): _bot.send_media_group(
                    chat_id, m, reply_to_message_id=r
                )
            )
        except Exception as error:
            logger.error("альбом вложений не отправился: {}", error)


async def _notify(user_id: int, text: str, *, summary: str | None = None) -> None:
    chat_id = await _telegram_id(user_id)
    if chat_id is None:
        logger.warning("уведомление: нет пользователя id={}", user_id)
        return
    login = await _user_login(user_id)
    tag = f"user={user_id} tg={chat_id}"
    if login:
        tag += f" login={login}"
    logger.info(
        "отправка → {} | {}",
        tag,
        summary if summary is not None else text,
    )
    await _send(chat_id, text)


async def _on_service_status(event: ServiceStatusChanged) -> None:
    """Сервис поднялся/упал — уведомить админов (если включено в админке)."""
    async with session_factory() as session:
        uow = UnitOfWork(session)
        row = await uow.config.get_global()
    if row is None or not row.notify_service_status:
        return
    icon = "🟢" if event.is_up else "🔴"
    verb = "поднялся" if event.is_up else "упал"
    since = f" (с {event.since})" if event.since else ""
    await notify_admins(
        _bot, f"{icon} Сервис «{event.service.value}» {verb}{since}"
    )


_HANDLERS: list[tuple[type[Event], object]] = [
    (MarksChanged, _on_marks),
    (AbsencesChanged, _on_absences),
    (MailReceived, _on_mail),
    (Auth2FARequired, _on_2fa_required),
    (Auth2FACodeResent, _on_auth),
    (Auth2FAFailed, _on_auth),
    (AuthCompleted, _on_auth),
    (AuthFailed, _on_auth),
    (ReminderDue, _on_reminder),
    (LessonsCheckResult, _on_auth),
    (ServiceStatusChanged, _on_service_status),
]


def register_telegram_notifier(bot: Bot) -> None:
    """Подписывает отправку в Telegram на уведомительные события."""
    global _bot
    _bot = bot
    for event_cls, handler in _HANDLERS:
        subscribe(event_cls)(handler)  # type: ignore[arg-type]
    logger.info("telegram-notifier | подписан на {} событий", len(_HANDLERS))
