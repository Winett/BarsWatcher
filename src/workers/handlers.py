"""Обработчики проверок данных (события worker-очереди).

Цикл одной проверки:
  circuit breaker → восстановление сессии/авторизация (с 2FA) → fetch →
  diff со снимками → события изменений (в bot-очередь) → фиксация состояния.

Ошибки:
  - транзиентная/недоступность → интервал ×1.5 (cap max), журнал доступности;
  - авторизация → re-login один раз, затем INVALID_CREDS + событие auth.failed.

2FA (БАРС):
  - auth.2fa_submitted      → проверка кода пользователя → auth.completed /
                              auth.2fa_failed;
  - auth.2fa_resend_requested → повторная отправка кода → auth.2fa_code_resent.
"""

from __future__ import annotations

import fnmatch
import os
import time
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta

from loguru import logger

from src.core.config import settings
from src.db.database import session_factory
from src.db.models import ServiceKind, WatchStates
from src.events import (
    AbsencesChanged,
    Auth2FACodeResent,
    Auth2FAFailed,
    Auth2FARequired,
    Auth2FAResendRequested,
    Auth2FASubmitted,
    AuthCompleted,
    AuthFailed,
    CheckRequested,
    LessonsCheckRequested,
    LessonsCheckResult,
    MailReceived,
    MarksChanged,
    PollDue,
    ServiceStatusChanged,
    get_redis,
    publish,
    subscribe,
)
from src.integrations import (
    OSEP_IGNORE_FOLDERS,
    AuthError,
    AuthStatus,
    DataParseError,
    ServiceUnavailableError,
    TwoFactorRequiredError,
    absences_to_snapshot,
    get_bars_client,
    get_osep_client,
    marks_to_snapshot,
)
from src.integrations.bars.parser import current_semester_title
from src.repositories.uow import UnitOfWork
from src.services import (
    AvailabilityTracker,
    SnapshotStore,
    apply_hidden_mark,
    diff_bars_absences,
    diff_bars_marks,
    diff_folder_counts,
    resolve_poll_settings,
)
from src.services.attachments import AttachmentCache

log = logger.bind(component="checks")

__all__ = ["run_check", "check_marks", "check_mail"]


# ────────────────────────────── подписки ──────────────────────────────


@subscribe(PollDue)
async def on_poll_due(event: PollDue) -> None:
    log.info("проверка {} | user={}", event.service.value, event.user_id)
    await run_check(event.user_id, event.service)


@subscribe(CheckRequested)
async def on_check_requested(event: CheckRequested) -> None:
    log.info("ручная проверка {} | user={}", event.service.value, event.user_id)
    await run_check(event.user_id, event.service)


@subscribe(LessonsCheckRequested)
async def on_lessons_check(event: LessonsCheckRequested) -> None:
    """Сверка пар журнала с расписанием (для ответственного за журнал)."""
    from src.services.lessons import find_lesson_issues, semester_start

    log.info("сверка пар: запрос (за {} дней) | user={}", event.days, event.user_id)
    date_to = date.today()
    if event.days <= 0:
        # сверка пар — с начала семестра по текущую дату
        date_from = semester_start(date_to)
    else:
        # days=1 — «за сегодня» (только сегодняшний день)
        date_from = date_to - timedelta(days=event.days - 1)
    period = f"{date_from.strftime('%d.%m.%Y')}–{date_to.strftime('%d.%m.%Y')}"

    client, ok = await authed_bars_client(event.user_id)
    if not ok:
        await publish(
            LessonsCheckResult(
                user_id=event.user_id,
                period=period,
                days=event.days,
                error="не удалось авторизоваться в БАРС (возможно, требуется 2FA)",
            )
        )
        return

    try:
        # пагинация 500: занятия журнала за период (докачка страниц)
        lessons = await client.fetch_lessons_range(date_from, date_to)
        schedule = await client.fetch_schedule(date_from, date_to)
    except Exception as error:
        await publish(
            LessonsCheckResult(
                user_id=event.user_id,
                period=period,
                days=event.days,
                error=str(error)[:200],
            )
        )
        return

    issues = find_lesson_issues(schedule, lessons)
    await publish(
        LessonsCheckResult(
            user_id=event.user_id,
            period=period,
            days=event.days,
            issues=[issue.to_dict() for issue in issues],
        )
    )
    log.info(
        "сверка пар: готово | user={} | занятий={} пар расписания={} проблем={}",
        event.user_id,
        len(lessons),
        len(schedule),
        len(issues),
    )


@subscribe(Auth2FASubmitted)
async def on_auth_2fa_submitted(event: Auth2FASubmitted) -> None:
    if event.service != ServiceKind.BARS:
        log.warning("2FA для {} не поддерживается", event.service.value)
        return
    async with session_factory() as session:
        uow = UnitOfWork(session)
        user = await uow.users.get_by_id(
            event.user_id, with_bars_credentials=True, with_bars_state=True
        )
        state = getattr(user, "bars_state", None)
        if user is None or state is None:
            return
        client = get_bars_client(event.user_id)
        _restore_session(state, client)
        try:
            result = await client.verify_2fa_code(event.code)
        except (ServiceUnavailableError, ConnectionError, TimeoutError, DataParseError) as error:
            log.warning("проверка 2FA не удалась: {}", error)
            return
        except AuthError as error:
            state.last_error_message = str(error)[:255]
            state.last_error_datetime = datetime.now(UTC)
            await uow.commit()
            await publish(
                Auth2FAFailed(user_id=event.user_id, service=event.service)
            )
            return

        _save_session(state, client)
        if result.status == AuthStatus.OK:
            state.status = WatchStates.ACTIVE
            state.last_error_message = None
            if await uow.bars_profile.get_by_user_id(event.user_id) is None:
                try:
                    await _save_profile(uow, event.user_id, await client.fetch_profile())
                except (ServiceUnavailableError, AuthError, DataParseError) as error:
                    log.warning("профиль БАРС не сохранён: {}", error)
            await uow.commit()
            await publish(
                AuthCompleted(user_id=event.user_id, service=event.service)
            )
            log.info("2FA принят, отслеживание активно | user={}", event.user_id)
        else:
            await uow.commit()
            await publish(
                Auth2FAFailed(user_id=event.user_id, service=event.service)
            )
            log.info("2FA код неверный | user={}", event.user_id)


@subscribe(Auth2FAResendRequested)
async def on_auth_2fa_resend(event: Auth2FAResendRequested) -> None:
    if event.service != ServiceKind.BARS:
        return
    async with session_factory() as session:
        uow = UnitOfWork(session)
        user = await uow.users.get_by_id(event.user_id, with_bars_state=True)
        state = getattr(user, "bars_state", None)
        if user is None or state is None:
            return
        client = get_bars_client(event.user_id)
        _restore_session(state, client)
        try:
            result = await client.send_2fa_code()
        except (ServiceUnavailableError, AuthError, DataParseError) as error:
            log.warning("повторная отправка 2FA не удалась: {}", error)
            return
        _save_session(state, client)
        await uow.commit()
        await publish(
            Auth2FACodeResent(
                user_id=event.user_id,
                service=event.service,
                destination=result.destination,
            )
        )


async def run_check(user_id: int, service: ServiceKind) -> None:
    if service == ServiceKind.BARS:
        await check_marks(user_id)
    else:
        await check_mail(user_id)


# ────────────────────────────── БАРС ──────────────────────────────


async def check_marks(user_id: int) -> None:
    async with session_factory() as session:
        uow = UnitOfWork(session)
        user = await uow.users.get_by_id(
            user_id,
            with_bars_credentials=True,
            with_bars_state=True,
            with_bars_settings=True,
        )
        state = getattr(user, "bars_state", None)
        if user is None or state is None:
            log.warning("check_marks: нет состояния | user={}", user_id)
            return
        if state.status != WatchStates.ACTIVE:
            log.debug(
                "check_marks: статус {} — пропуск | user={}",
                state.status.value,
                user_id,
            )
            return
        creds = user.bars_credentials
        if creds is None or not creds.login:
            log.warning("check_marks: нет кредов | user={}", user_id)
            return
        tag = _user_tag(user, creds)

        poll = await resolve_poll_settings(session, user_id)
        now = datetime.now(UTC)
        redis = await get_redis()
        tracker = AvailabilityTracker(redis)
        client = get_bars_client(user_id)
        _restore_session(state, client)

        # проба доступности перед проверкой: недоступный сервис не опрашивается
        t0 = time.monotonic()
        ok = await client.check_availability()
        probe_ms = _ms(t0)
        await _report_availability(uow, tracker, ServiceKind.BARS, ok, now)
        if not ok:
            _schedule_next(state, poll, now, error=True)
            await uow.commit()
            log.info(
                "check_marks: сервис недоступен, отложено (проба {}ms) | {}",
                probe_ms, tag,
            )
            return

        # авторизация (с обработкой 2FA)
        if not await client.is_authenticated():
            result = await client.authenticate(creds.login, creds.password or "")
            if result.status == AuthStatus.TWO_FACTOR_REQUIRED:
                await _enter_2fa(uow, state, client, user_id)
                return
            if result.status == AuthStatus.INVALID_CREDENTIALS:
                await _on_auth_failed(uow, state, now, user_id, ServiceKind.BARS)
                return
            _save_session(state, client)

        # профиль (ФИО/группа/ответственный за журнал) — один раз
        if await uow.bars_profile.get_by_user_id(user_id) is None:
            try:
                await _save_profile(uow, user_id, await client.fetch_profile())
            except (ServiceUnavailableError, AuthError, DataParseError) as error:
                log.warning("профиль БАРС не сохранён: {}", error)

        # fetch: оценки и пропуски
        t1 = time.monotonic()
        try:
            marks_snapshot = await client.fetch_marks()
            absences_snapshot = await client.fetch_absences()
        except TwoFactorRequiredError:
            # перелогин после разлогина потребовал 2FA — нужен новый код
            await _enter_2fa(uow, state, client, user_id)
            return
        except (ServiceUnavailableError, ConnectionError, TimeoutError) as error:
            await _on_iteration_error(
                uow, state, poll, now, error,
                service=ServiceKind.BARS, tracker=tracker, unavailable=True,
            )
            await uow.commit()
            return
        except DataParseError as error:
            # неожиданный формат ответа — не ошибка авторизации и не «сервер упал»
            await _on_iteration_error(
                uow, state, poll, now, error, service=ServiceKind.BARS
            )
            await uow.commit()
            return
        except AuthError as error:
            await _on_iteration_error(
                uow, state, poll, now, error, service=ServiceKind.BARS
            )
            await uow.commit()
            return
        _save_session(state, client)

        # diff со снимками (метка семестра — для распознавания его смены)
        semester = _semester_label()
        marks_dict = marks_to_snapshot(marks_snapshot, semester=semester)
        absences_dict = absences_to_snapshot(absences_snapshot, semester=semester)
        store = SnapshotStore(redis)
        old_marks = await store.get(store.marks_key(user_id))
        old_absences = await store.get(store.absences_key(user_id))
        mark_changes = diff_bars_marks(old_marks, marks_dict)
        absence_changes = diff_bars_absences(old_absences, absences_dict)
        settings_row = user.bars_settings

        if old_marks is not None:
            if mark_changes and (settings_row is None or settings_row.notify_marks):
                changes = (
                    apply_hidden_mark(mark_changes)
                    if settings_row is not None and settings_row.hidden_mark
                    else mark_changes
                )
                await publish(MarksChanged(user_id=user_id, changes=changes))
        if old_absences is not None:
            if absence_changes and (
                settings_row is None or settings_row.notify_absences
            ):
                await publish(AbsencesChanged(user_id=user_id, changes=absence_changes))
        await store.set(store.marks_key(user_id), marks_dict)
        await store.set(store.absences_key(user_id), absences_dict)

        await _report_availability(uow, tracker, ServiceKind.BARS, True, now)
        _schedule_next(state, poll, now, error=False)
        await uow.commit()
        fetch_ms = _ms(t1)
        if fetch_ms > SLOW_RESPONSE_MS:
            log.warning("медленный ответ БАРС: {}ms | {}", fetch_ms, tag)
        log.info(
            "check_marks: {} | предметов={} | изменений оценок={} пропусков={} | {}ms",
            tag, len(marks_snapshot), len(mark_changes), len(absence_changes), fetch_ms,
        )


# ────────────────────────────── ОСЭП ──────────────────────────────


async def check_mail(user_id: int) -> None:
    async with session_factory() as session:
        uow = UnitOfWork(session)
        user = await uow.users.get_by_id(
            user_id,
            with_osep_credentials=True,
            with_osep_state=True,
            with_osep_settings=True,
        )
        state = getattr(user, "osep_state", None)
        if user is None or state is None:
            log.warning("check_mail: нет состояния | user={}", user_id)
            return
        if state.status != WatchStates.ACTIVE:
            log.debug(
                "check_mail: статус {} — пропуск | user={}",
                state.status.value,
                user_id,
            )
            return
        creds = user.osep_credentials
        if creds is None or not creds.login:
            log.warning("check_mail: нет кредов | user={}", user_id)
            return
        tag = _user_tag(user, creds)

        poll = await resolve_poll_settings(session, user_id)
        now = datetime.now(UTC)
        redis = await get_redis()
        tracker = AvailabilityTracker(redis)
        client = get_osep_client(user_id)
        _restore_session(state, client)

        # проба доступности перед проверкой: недоступный сервис не опрашивается
        t0 = time.monotonic()
        ok = await client.check_availability()
        probe_ms = _ms(t0)
        await _report_availability(uow, tracker, ServiceKind.OSEP, ok, now)
        if not ok:
            _schedule_next(state, poll, now, error=True)
            await uow.commit()
            log.info(
                "check_mail: сервис недоступен, отложено (проба {}ms) | {}",
                probe_ms, tag,
            )
            return

        if not await client.is_authenticated():
            result = await client.authenticate(creds.login, creds.password or "")
            if result.status == AuthStatus.INVALID_CREDENTIALS:
                await _on_auth_failed(uow, state, now, user_id, ServiceKind.OSEP)
                return
            _save_session(state, client)

        try:
            t1 = time.monotonic()
            counts = await client.fetch_folder_counts()
            store = SnapshotStore(redis)
            old = await store.get(store.folders_key(user_id))
            if old is None:
                # первая проверка (или после включения ОСЭП): база папок
                # запоминается без уведомлений — «старые» письма не шлём
                log.info(
                    "check_mail: базовый снапшот папок сохранён (без уведомлений) | user={}",
                    user_id,
                )
            grown = diff_folder_counts(old, counts, OSEP_IGNORE_FOLDERS)

            blacklist = (
                list(user.osep_settings.blacklist or [])
                if user.osep_settings is not None
                else []
            )
            sent = 0
            for folder, delta in grown.items():
                for mail in await client.fetch_new_mails(folder, delta):
                    if _is_blacklisted(mail.sender_email, blacklist):
                        continue
                    # вложения скачиваем сразу (как в master): сессия свежая,
                    # бот отправит файлы из общего кэша без повторного входа
                    await _prefetch_attachments(user_id, mail.attachments, client)
                    await publish(
                        MailReceived(
                            user_id=user_id, folder=folder, mail=asdict(mail)
                        )
                    )
                    sent += 1
            await store.set(store.folders_key(user_id), counts)
        except (ServiceUnavailableError, ConnectionError, TimeoutError) as error:
            await _on_iteration_error(
                uow, state, poll, now, error,
                service=ServiceKind.OSEP, tracker=tracker, unavailable=True,
            )
            await uow.commit()
            return
        except (AuthError, DataParseError) as error:
            await _on_iteration_error(
                uow, state, poll, now, error, service=ServiceKind.OSEP
            )
            await uow.commit()
            return
        _save_session(state, client)

        await _report_availability(uow, tracker, ServiceKind.OSEP, True, now)
        _schedule_next(state, poll, now, error=False)
        await uow.commit()
        fetch_ms = _ms(t1)
        if fetch_ms > SLOW_RESPONSE_MS:
            log.warning("медленный ответ ОСЭП: {}ms | {}", fetch_ms, tag)
        log.info(
            "check_mail: {} | папок={} | новых писем={} | {}ms",
            tag, len(counts), sent, fetch_ms,
        )


# ────────────────────────────── общее ──────────────────────────────


async def _enter_2fa(uow, state, client, user_id: int) -> None:
    """Перевести БАРС в ожидание кода 2FA и уведомить пользователя."""
    destination = None
    try:
        send_result = await client.send_2fa_code()
        destination = send_result.destination
    except (ServiceUnavailableError, AuthError, DataParseError) as error:
        log.warning("не удалось отправить код 2FA: {}", error)
    state.status = WatchStates.WAITING_2FA
    _save_session(state, client)
    await uow.commit()
    await publish(
        Auth2FARequired(
            user_id=user_id,
            service=ServiceKind.BARS,
            destination=destination,
        )
    )
    log.info("check_marks: нужен 2FA | user={}", user_id)


async def authed_bars_client(user_id: int):
    """Клиент БАРС с восстановленной сессией и авторизацией (best-effort).

    Сессия восстанавливается из БД — её мог сохранить бот или прошлая проверка,
    поэтому «уже есть авторизация» действительно работает. Возвращает
    (client, ok): ok=False, если авторизоваться не удалось (например, 2FA).
    """
    async with session_factory() as session:
        uow = UnitOfWork(session)
        user = await uow.users.get_by_id(
            user_id, with_bars_credentials=True, with_bars_state=True
        )
    if user is None:
        return None, False
    creds = user.bars_credentials
    if creds is None or not creds.login:
        return None, False
    state = getattr(user, "bars_state", None)
    client = get_bars_client(user_id)
    if state is not None:
        _restore_session(state, client)
    try:
        if not await client.is_authenticated():
            result = await client.authenticate(creds.login, creds.password or "")
            if result.status is not AuthStatus.OK:
                return client, False
    except Exception as error:
        log.warning("авторизация БАРС не удалась | user={}: {}", user_id, error)
        return client, False
    return client, True


def _restore_session(state, client) -> None:
    # pending_2fa есть только у БАРС (2FA в ОСЭП нет)
    client.restore_session(
        {
            "cookies": state.cookies,
            "pending_2fa": getattr(state, "pending_2fa", None),
        }
    )


def _save_session(state, client) -> None:
    blob = client.export_session()
    state.cookies = blob.get("cookies") or {}
    if hasattr(state, "pending_2fa"):  # 2FA только у БАРС
        state.pending_2fa = blob.get("pending_2fa")


_attachment_cache = AttachmentCache()  # общий каталог с ботом (attachments_cache/)

SLOW_RESPONSE_MS = 5000  # дольше — предупреждение «медленный ответ»


def _user_tag(user, creds=None) -> str:
    """Идентификация пользователя для логов: id, telegram_id, @username, логин."""
    parts = [f"user={user.id}", f"tg={user.telegram_id}"]
    if getattr(user, "username", None):
        parts.append(f"@{user.username}")
    if creds is not None and getattr(creds, "login", None):
        parts.append(f"login={creds.login}")
    return " ".join(parts)


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


async def _prefetch_attachments(user_id: int, attachments: list, client) -> None:
    """Скачать вложения письма в общий кэш (best-effort).

    Как в master: файлы забираются сразу при обнаружении письма — сессия
    свежая; бот потом просто отправляет их из кэша.
    """
    for att in attachments:
        attachment_id = getattr(att, "attachment_id", "") or ""
        filename = getattr(att, "filename", "") or "file"
        size = int(getattr(att, "size", 0) or 0)
        try:
            await _attachment_cache.get_or_download(
                attachment_id=attachment_id or filename,
                filename=filename,
                size=size,
                user_id=user_id,
                download=lambda a=attachment_id: client.download_attachment(a),
            )
        except Exception:
            log.warning("вложение {} не скачалось при обнаружении письма", filename)


def _is_blacklisted(email: str, patterns: list[str]) -> bool:
    email_lower = email.lower()
    return any(
        fnmatch.fnmatch(email_lower, pattern.lower()) for pattern in patterns
    )


def _semester_label() -> str:
    """Метка текущего семестра для снимков (ловит смену семестра в diff).

    SIM_SEMESTER — ручная подмена для тестов смены семестра; иначе текущий
    семестр по календарю — так же выбирает его БАРС-клиент.
    """
    return os.environ.get("SIM_SEMESTER") or current_semester_title()


def _schedule_next(state, poll, now: datetime, error: bool) -> None:
    """Интервал следующей проверки.

    Ошибка (или сервис недоступен) — часто, примерно раз в минуту, чтобы
    вовремя заметить восстановление. Успех: пока с последней ошибки не прошло
    3 минуты — те же 60с; как только 3 минуты идут только удачные запросы —
    стандартный интервал (пользовательский или глобальный).
    """
    if settings.integration_mode != "real":
        interval = settings.sim_poll_interval_seconds
        state.current_interval_seconds = interval
        state.next_check = now + timedelta(seconds=interval)
        state.last_check_at = now
        if not error:
            state.last_success_at = now
        return
    if error:
        # упавший/недоступный сервис проверяем примерно раз в минуту
        state.current_interval_seconds = 60
        state.next_check = now + timedelta(seconds=60)
        state.last_check_at = now
        state.last_error_datetime = now  # отсчёт «3 минуты удачных»
        return
    # успех: стандартный интервал — после 3 минут без ошибок
    last_error = state.last_error_datetime
    stable = last_error is None or (now - last_error) >= timedelta(minutes=3)
    current = poll.poll_interval_seconds if stable else 60
    state.current_interval_seconds = current
    state.next_check = now + timedelta(seconds=current)
    state.last_check_at = now
    state.last_success_at = now


async def _on_iteration_error(
    uow, state, poll, now: datetime, error: Exception,
    service: ServiceKind, tracker: AvailabilityTracker | None = None,
    unavailable: bool = False,
) -> None:
    state.last_error_message = str(error)[:255]
    state.last_error_datetime = now
    _schedule_next(state, poll, now, error=True)
    if unavailable and tracker is not None:
        await _report_availability(uow, tracker, service, False, now)
    log.warning(
        "ошибка проверки {} | {}: {}",
        service.value, type(error).__name__, error,
    )


async def _on_auth_failed(
    uow, state, now: datetime, user_id: int, service: ServiceKind
) -> None:
    state.status = WatchStates.INVALID_CREDS
    state.last_error_message = "Неверный логин или пароль"
    state.last_error_datetime = now
    await uow.commit()
    await publish(
        AuthFailed(
            user_id=user_id, service=service, reason="Неверный логин или пароль"
        )
    )
    log.warning("авторизация не удалась {} | user={}", service.value, user_id)


async def _report_availability(
    uow, tracker: AvailabilityTracker, service: ServiceKind, ok: bool, now: datetime
) -> None:
    transition = await tracker.report(service.value, ok, now)
    if not transition.changed:
        return

    # журнал: закрыть предыдущий интервал, открыть новый
    previous = await uow.monitoring.get_open_interval(
        service.value, not transition.is_up
    )
    if previous is not None:
        await uow.monitoring.close_interval(previous, transition.since)
    existing = await uow.monitoring.get_open_interval(
        service.value, transition.is_up
    )
    if existing is None:
        await uow.monitoring.open_interval(
            service.value, transition.is_up, transition.since
        )

    await publish(
        ServiceStatusChanged(
            service=service,
            is_up=transition.is_up,
            since=transition.since.isoformat(),
        )
    )
    log.info(
        "доступность {} → {} (с {})",
        service.value,
        "UP" if transition.is_up else "DOWN",
        transition.since.isoformat(),
    )


async def _save_profile(uow, user_id: int, profile: dict) -> None:
    from src.db.models import BarsProfile

    row = await uow.bars_profile.get_by_user_id(user_id)
    if row is None:
        row = BarsProfile(user_id=user_id)
        await uow.bars_profile.add(row)
    row.fio = profile.fio
    row.group = profile.group
    row.is_journal_responsible = bool(profile.is_journal_responsible)
