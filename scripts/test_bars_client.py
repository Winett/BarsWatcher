"""Интерактивный прогон реального клиента БАРС: авторизация, 2FA, данные.

Запуск:
  uv run python scripts/test_bars_client.py                    # меню
  uv run python scripts/test_bars_client.py marks absences     # сразу команды
  uv run python scripts/test_bars_client.py --login ИвановИИ   # логин сразу

Команды: session | profile | marks | absences | lessons | schedule | availability

Сессия между запусками («для одного человека»): по умолчанию скрипт пробует
восстановить куки из session.json — если они ещё валидны, вход не требуется;
иначе спрашивает логин/пароль и логинится заново. Файл обновляется при выходе.
  --session FILE — другой файл сессии;  --fresh — не читать сохранённую сессию.

Логин/пароль: спросит (или env BARS_LOGIN / BARS_PASSWORD / флаги --login/--password).

2FA: скрипт сам отправит код, покажет куда («Сообщение отправлено в ...»),
дождётся ввода, при неверном коде предложит повторить ввод или отправить снова.

Ошибки различаются намеренно:
  - «Сервер БАРС недоступен/упал» — сеть/5xx (это НЕ ошибка авторизации);
  - «Неверный логин или пароль» — сервер ответил, креды не подошли;
  - «Неожиданный формат ответа» — парсер не разобрал страницу.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("SECRET_KEY", "test-bars-client")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:test")
os.environ.setdefault("ADMINS", "[1]")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", "postgres")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "barswatcher")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dataclasses import asdict, is_dataclass  # noqa: E402
from datetime import date, timedelta  # noqa: E402

from src.integrations.bars import BarsWebClient  # noqa: E402
from src.integrations.ports import (  # noqa: E402
    AuthError,
    AuthStatus,
    DataParseError,
    ServiceUnavailableError,
    TwoFactorRequiredError,
)

SESSION_FILE = "session.json"


def _plain(data):
    """dataclass → dict для вывода JSON."""
    if is_dataclass(data) and not isinstance(data, type):
        return asdict(data)
    if isinstance(data, dict):
        return {key: _plain(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_plain(value) for value in data]
    return data


def show(title: str, data) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(_plain(data), ensure_ascii=False, indent=2, default=str)[:6000])


# ────────────────────────────── авторизация / 2FA ──────────────────────────────


async def wait_2fa(client: BarsWebClient) -> bool:
    """Отправить код, дождаться ввода. True — код принят."""
    try:
        send = await client.send_2fa_code()
        print(f"\nКод 2FA отправлен: {send.destination or '(неизвестно, куда)'}")
    except (ServiceUnavailableError, AuthError, DataParseError) as error:
        print(f"\nНе удалось отправить код: {error}")
        print("Всё равно можно попробовать ввести код вручную.")

    while True:
        print("Введите код 2FA (resend — отправить снова, exit — отмена): ", end="")
        code = input().strip()
        if code.lower() in {"exit", "q", "quit", "отмена"}:
            print("Отменено.")
            return False
        if code.lower() == "resend":
            try:
                send = await client.send_2fa_code()
                print(f"Код отправлен повторно: {send.destination}")
            except (ServiceUnavailableError, AuthError, DataParseError) as error:
                print(f"Не удалось отправить код: {error}")
            continue
        if not code:
            continue
        try:
            result = await client.verify_2fa_code(code)
        except (ServiceUnavailableError, AuthError, DataParseError) as error:
            print(f"Ошибка проверки кода: {error}")
            continue
        if result.status == AuthStatus.OK:
            print("Код принят — авторизация завершена ✅")
            return True
        print("Код введён неверно ❌ (повторите ввод или resend)")


async def ensure_auth(client: BarsWebClient, login: str, password: str) -> bool:
    """Проверить куки; если не валидны — войти (с 2FA при необходимости)."""
    try:
        if await client.is_authenticated():
            print("Сохранённая сессия валидна ✅ (вход не требуется)")
            return True
        print(f"Сессия не валидна. Вход в БАРС как «{login}»...")
        result = await client.authenticate(login, password)
    except ServiceUnavailableError as error:
        print(f"❌ Сервер БАРС недоступен/упал: {error}")
        print("   (это НЕ ошибка авторизации — попробуйте позже)")
        return False
    except DataParseError as error:
        print(f"❌ Неожиданный формат ответа сервера: {error}")
        print("   (это НЕ ошибка авторизации — похоже, страница не та)")
        return False
    except AuthError as error:
        print(f"❌ Ошибка авторизации: {error}")
        return False

    if result.status == AuthStatus.OK:
        print("Авторизация успешна ✅")
        return True
    if result.status == AuthStatus.INVALID_CREDENTIALS:
        print(
            "❌ Сервер отклонил вход"
            + (f": {result.error}" if result.error else " (неверный логин/пароль)")
        )
        return False
    if result.status == AuthStatus.TWO_FACTOR_REQUIRED:
        print("Требуется код 2FA 🔐")
        return await wait_2fa(client)
    print(f"Неожиданный статус: {result.status}")
    return False


# ────────────────────────────── команды ──────────────────────────────


async def cmd_session(client: BarsWebClient) -> None:
    ok = await client.is_authenticated()
    print("Сессия активна ✅" if ok else "Активной сессии нет (нужен вход)")
    session = client.export_session()
    print("Куки:", ", ".join(sorted(session.get("cookies") or {})) or "нет")
    if session.get("pending_2fa"):
        print("Ожидающее 2FA:", session["pending_2fa"])


async def cmd_profile(client: BarsWebClient) -> None:
    profile = await client.fetch_profile()
    show("Профиль (ФИО, группа, ответственный за журнал)", profile)
    if profile.is_journal_responsible:
        print("→ ответственный за журнал (может изменять пары)")


async def cmd_marks(client: BarsWebClient) -> None:
    marks = await client.fetch_marks()
    show(f"Оценки: {len(marks)} дисциплин", marks)
    with_marks = sum(
        1
        for d in marks.values()
        for m in d.marks
        if m.mark or m.rewriting
    )
    print(f"Контрольных мероприятий с оценкой: {with_marks}")


async def cmd_absences(client: BarsWebClient) -> None:
    absences = await client.fetch_absences()
    show(f"Пропуски: {len(absences)} записей", absences)


async def cmd_lessons(client: BarsWebClient) -> None:
    lessons = await client.fetch_today_lessons()
    show(f"Занятия журнала за сегодня: {len(lessons)}", lessons)
    print("\nПризнак «пара не проставлена»:")
    found = False
    for lesson in lessons:
        if lesson.status in {"на рассмотрении", "требуется корректировка"} and not lesson.absentees:
            print(f"  ⚠ {lesson.lesson} — статус «{lesson.status}», отсутствующих нет")
            found = True
    if not found:
        print("  (не найдено)")


async def cmd_schedule(client: BarsWebClient) -> None:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    print(f"Расписание {monday:%d.%m.%Y} — {sunday:%d.%m.%Y}")
    schedule = await client.fetch_schedule(monday, sunday)
    show(f"Расписание: {len(schedule)} занятий", schedule)


async def cmd_availability(client: BarsWebClient) -> None:
    ok = await client.check_availability()
    print("БАРС доступен ✅" if ok else "БАРС НЕ доступен ❌")


COMMANDS: dict[str, tuple] = {
    "session": (cmd_session, "сессия/куки"),
    "profile": (cmd_profile, "ФИО, группа, ответственный за журнал"),
    "marks": (cmd_marks, "оценки (КМ, переписывания, балл, итоговая)"),
    "absences": (cmd_absences, "пропуски"),
    "lessons": (cmd_lessons, "занятия журнала за сегодня"),
    "schedule": (cmd_schedule, "расписание на текущую неделю"),
    "availability": (cmd_availability, "доступность сервиса"),
}


async def run_action(client: BarsWebClient, action, label: str) -> None:
    print(f"\n── {label} ──")
    try:
        await action(client)
    except TwoFactorRequiredError:
        print("Сессия слетела и потребовался повторный 2FA 🔐")
        if await wait_2fa(client):
            await action(client)
    except ServiceUnavailableError as error:
        print(f"❌ Сервер БАРС недоступен/упал: {error}")
        print("   (это НЕ ошибка авторизации)")
    except DataParseError as error:
        print(f"❌ Неожиданный формат ответа: {error}")
    except AuthError as error:
        print(f"❌ Ошибка авторизации/сессии: {error}")
    except Exception as error:  # noqa: BLE001 — тестовый скрипт: показать всё
        print(f"Ошибка: {type(error).__name__}: {error}")


async def menu(client: BarsWebClient, args) -> None:
    creds = {
        "login": args.login or os.environ.get("BARS_LOGIN") or "",
        "password": args.password or os.environ.get("BARS_PASSWORD") or "",
    }
    while True:
        print("\n════ Меню тестирования клиента БАРС ════")
        for i, (name, (_, label)) in enumerate(COMMANDS.items(), start=1):
            print(f"  {i}) {name:<12} — {label}")
        print("  r) перелогин (сбросить сессию)")
        print("  q) выход")
        choice = input("Выбор: ").strip().lower()
        if choice in {"q", "quit", "exit", "й"}:
            return
        if choice == "r":
            if not creds["login"]:
                creds["login"], creds["password"] = get_credentials(args)
            await ensure_auth(client, creds["login"], creds["password"])
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(COMMANDS):
            name = list(COMMANDS)[int(choice) - 1]
            action, label = COMMANDS[name]
            await run_action(client, action, label)
        else:
            print("Неизвестная команда")


# ────────────────────────────── запуск ──────────────────────────────


def get_credentials(args) -> tuple[str, str]:
    login = args.login or os.environ.get("BARS_LOGIN") or ""
    while not login:
        login = input("Логин БАРС: ").strip()
        if not login:
            print("Логин не может быть пустым — попробуйте ещё раз.")
    password = args.password or os.environ.get("BARS_PASSWORD") or ""
    while not password:
        # видимый ввод надёжнее getpass во всех консолях (пароль будет виден)
        password = input("Пароль БАРС (видимый ввод): ")
        if not password:
            print("Пароль не может быть пустым — попробуйте ещё раз.")
    return login, password


def load_session(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as file:
            session = json.load(file)
        print(f"Найден файл сессии {path} — пробую куки...")
        return session
    except (OSError, ValueError) as error:
        print(f"Не удалось прочитать {path}: {error}")
        return None


def save_session(path: str, client: BarsWebClient) -> None:
    try:
        with open(path, "w", encoding="utf-8") as file:
            json.dump(client.export_session(), file, ensure_ascii=False, indent=2)
        print(f"\nСессия сохранена в {path}")
    except OSError as error:
        print(f"Не удалось сохранить сессию: {error}")


async def run(args) -> None:
    client = BarsWebClient()

    if not args.fresh:
        session = load_session(args.session)
        if session:
            client.restore_session(session)

    try:
        # 1) пробуем сохранённые куки; 2) если мертвы — логин/пароль
        try:
            valid = await client.is_authenticated()
        except ServiceUnavailableError as error:
            print(f"❌ Сервер БАРС недоступен/упал: {error}")
            return
        if valid:
            print("Куки валидны — работаем без входа ✅")
        else:
            login, password = get_credentials(args)
            if not await ensure_auth(client, login, password):
                save_session(args.session, client)  # запомним хотя бы попытку 2FA
                return

        if args.commands:
            for name in args.commands:
                action, label = COMMANDS[name]
                await run_action(client, action, label)
        else:
            await menu(client, args)
    finally:
        save_session(args.session, client)
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Тест клиента БАРС: авторизация (с 2FA) и получение данных",
    )
    parser.add_argument(
        "commands",
        nargs="*",
        choices=list(COMMANDS),
        help="команды без меню (по умолчанию — интерактивное меню)",
    )
    parser.add_argument("--login", help="логин БАРС (или env BARS_LOGIN)")
    parser.add_argument("--password", help="пароль БАРС (или env BARS_PASSWORD)")
    parser.add_argument(
        "--session",
        default=SESSION_FILE,
        metavar="FILE",
        help=f"файл сессии (куки) между запусками, по умолчанию {SESSION_FILE}",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="не читать сохранённую сессию — войти заново",
    )
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nПрервано.")


if __name__ == "__main__":
    main()
