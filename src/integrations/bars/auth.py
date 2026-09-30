"""Авторизация в БАРС (https://bars.mpei.ru/bars_web) — вход и 2FA.

Поток входа:
  GET  /bars_web/                     → __RequestVerificationToken
  POST /bars_web/ (token, Account, Password, RememberMe, StopOpenDefault)
      → нет куки ses_bars               → вход не подтверждён (НЕ обязательно
        неверный пароль: неверными креды считаем только если сервер сам явно
        написал об ошибке; иначе — AuthError с полной диагностикой)
      → ses_bars + auth_bars            → авторизованы
      → ses_bars без auth_bars          → требуется 2FA
2FA:
  a#btnSend onclick → tid (второй элемент с конца);
  GET  /bars_web/Auth/JSON_SendAF2_Code?tid=... → «Сообщение отправлено в ...»;
  POST /bars_web/Auth/LoginCode (token, Account, AF2_Code)
      → появление куки auth_bars = код верный, иначе — неверный (берём новый токен).
Разлогин: любой запрос данных отвечает 302 на /bars_web/.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
from loguru import logger

from src.integrations.models import TwoFASendResult
from src.integrations.ports import (
    AuthError,
    AuthResult,
    AuthStatus,
    DataParseError,
    ServiceUnavailableError,
)

from ..httplog import attach_http_logging
from .parser import extract_2fa_tid, extract_token

__all__ = ["BarsAuth", "SessionExpired"]

log = logger.bind(component="bars-auth")


def _raise_for_status(response: httpx.Response, what: str) -> None:
    """5xx/429 — сервис упал; это НЕ ошибка авторизации."""
    if response.status_code >= 500 or response.status_code == 429:
        raise ServiceUnavailableError(f"{what}: HTTP {response.status_code}")


def _strip_html_tags(text: str) -> str:
    """Убрать HTML-теги (<br/> и т.п.): текст уходит в Telegram-сообщение."""
    return " ".join(re.sub(r"<[^>]+>", " ", str(text)).split())


# маркеры текста, по которым БАРС сам сообщает об ошибке входа
_ERROR_PHRASES = re.compile(
    r"[^.\n]{0,80}(?:неверн\w*|не верн\w*|не указан\w*|не найден\w*|"
    r"не существует|заблокирован\w*|доступ запрещ\w*|не зарегистрирован\w*)[^.\n]{0,80}",
    re.IGNORECASE,
)


def _extract_error_text(content: str) -> str | None:
    """Текст ошибки на вернувшейся странице входа (если сервер её показал)."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(content, "html.parser")
    # шаблоны уведомлений bootstrap-notify — не ошибки сервера
    for node in soup.select("[data-notify]"):
        node.decompose()
    texts: list[str] = []
    for selector in (
        "div.validation-summary-errors li",
        "div.validation-summary-errors span",
        ".field-validation-error",
        ".alert-danger",
        ".alert-error",
        ".text-danger",
    ):
        for node in soup.select(selector):
            text = node.get_text(" ", strip=True)
            if text:
                texts.append(text)
    if texts:
        return " / ".join(dict.fromkeys(texts))
    # запасной вариант — фразы об ошибке по видимому тексту (без скриптов)
    for tag in soup(["script", "style"]):
        tag.decompose()
    match = _ERROR_PHRASES.search(soup.get_text(" ", strip=True))
    return match.group(0).strip() if match else None


def _describe_response(
    response: httpx.Response, cookies: dict, content: str
) -> str:
    """Диагностика ответа входа: что сервер прислал на самом деле."""
    markers = []
    if extract_token(content):
        markers.append("форма входа")
    if extract_2fa_tid(content):
        markers.append("форма 2FA")
    location = response.headers.get("location", "")
    return (
        f"HTTP {response.status_code} | url={response.url}"
        + (f" | location={location}" if location else "")
        + f" | куки: {sorted(cookies) or 'нет'}"
        + f" | тело: {len(content)} симв."
        + f" | признаки: {', '.join(markers) or 'нет'}"
    )


def _save_failure_dump(content: str) -> str | None:
    """Сохранить ответ входа в файл, чтобы можно было разобрать проблему."""
    try:
        path = Path("failed_login.html")
        path.write_text(content, encoding="utf-8")
        return str(path.resolve())
    except OSError:
        return None


class SessionExpired(Exception):
    """302 на страницу входа — сессия слетела, нужен re-login."""


class BarsAuth:
    BASE_URL = "https://bars.mpei.ru"
    APP = "/bars_web"

    def __init__(self, timeout: float = 30.0):
        self._client = httpx.AsyncClient(
            base_url=self.BASE_URL,
            timeout=timeout,
            follow_redirects=False,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
                )
            },
        )
        attach_http_logging(self._client, "БАРС")
        self._pending_token: str | None = None
        self._pending_tid: str | None = None
        self._account: str | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    async def close(self) -> None:
        await self._client.aclose()

    # ───────────────────────────── сессия ─────────────────────────────

    def export_session(self) -> dict:
        cookies = {cookie.name: cookie.value for cookie in self._client.cookies.jar}
        pending = None
        if self._pending_tid:
            pending = {
                "token": self._pending_token,
                "tid": self._pending_tid,
                "account": self._account,
            }
        return {"cookies": cookies, "pending_2fa": pending}

    def restore_session(self, session: dict | None) -> None:
        if not session:
            return
        # приоритет — живое состояние клиента: восстанавливаем только то,
        # чего у клиента ещё нет (иначе затрём свежие куки/токен старыми)
        existing = self._cookies()
        for name, value in (session.get("cookies") or {}).items():
            if name not in existing:
                self._client.cookies.set(name, value, domain="bars.mpei.ru")
        pending = session.get("pending_2fa") or {}
        if pending and not self._pending_token:
            self._pending_token = pending.get("token")
            self._pending_tid = pending.get("tid")
            self._account = pending.get("account")

    def _cookies(self) -> dict:
        return {cookie.name: cookie.value for cookie in self._client.cookies.jar}

    # ───────────────────────────── вход ─────────────────────────────

    async def is_authenticated(self) -> bool:
        try:
            response = await self._client.get(
                f"{self.APP}/ST/Student/ListStudent", follow_redirects=False
            )
        except httpx.HTTPError as error:
            raise ServiceUnavailableError(str(error)) from error
        return response.status_code == 200

    async def authenticate(self, login: str, password: str) -> AuthResult:
        if not login or not password:
            # пустые креды — частая причина «вход не удался», ловим явно
            raise AuthError(
                f"пустые креды: login={login!r} (длина {len(login)}), "
                f"пароль задан: {bool(password)}"
            )
        log.info(
            "БАРС: попытка входа | login={!r} (длина {}), пароль: {} симв.",
            login,
            len(login),
            len(password),
        )
        self._client.cookies.clear()
        self._account = login
        try:
            response = await self._client.get(f"{self.APP}/")
            _raise_for_status(response, "страница входа")
            token = extract_token(response.text)
            if not token:
                raise DataParseError("страница входа без __RequestVerificationToken")

            response = await self._client.post(
                f"{self.APP}/",
                data={
                    "__RequestVerificationToken": token,
                    "StopOpenDefault": "False",
                    "Account": login,
                    "Password": password,
                    "RememberMe": "True",
                },
                follow_redirects=False,  # как в master (allow_redirects=False)
            )
            _raise_for_status(response, "POST входа")
            content = response.text
        except httpx.HTTPError as error:
            raise ServiceUnavailableError(str(error)) from error

        cookies = self._cookies()
        details = _describe_response(response, cookies, content)
        if "ses_bars" not in cookies:
            error_text = _extract_error_text(content)
            if error_text:
                # сервер сам сообщил, что не так — это и есть неверные креды
                log.warning(
                    "БАРС: вход отклонён сервером | login={!r} | ошибка: {} | {}",
                    login,
                    error_text,
                    details,
                )
                return AuthResult(AuthStatus.INVALID_CREDENTIALS, error=error_text)
            if not extract_token(content):
                raise DataParseError(f"ответ входа без кук и без формы входа | {details}")
            # форма входа вернулась без явной ошибки: НЕ факт, что пароль неверный
            dump = _save_failure_dump(content)
            raise AuthError(
                "сервер не выдал ses_bars и не сообщил причину "
                "(это ещё не значит, что логин/пароль неверные) | "
                f"{details}"
                + (f" | ответ сохранён: {dump}" if dump else "")
            )

        if "auth_bars" in cookies:
            log.info("БАРС: вход выполнен | login={!r} | {}", login, details)
            return AuthResult(AuthStatus.OK)

        # требуется 2FA: новый токен + tid из кнопки отправки кода
        token = extract_token(content)
        tid = extract_2fa_tid(content)
        if not token or not tid:
            raise DataParseError(f"не удалось разобрать страницу 2FA | {details}")
        self._pending_token = token
        self._pending_tid = tid
        log.info("БАРС: требуется 2FA (tid={}) | login={!r}", tid, login)
        return AuthResult(AuthStatus.TWO_FACTOR_REQUIRED)

    # ───────────────────────────── 2FA ─────────────────────────────

    async def send_2fa_code(self) -> TwoFASendResult:
        """Отправить код пользователю (destination — «Сообщение отправлено в ...»)."""
        if not self._pending_tid:
            raise AuthError("нет ожидающего 2FA-состояния")
        try:
            response = await self._client.get(
                f"{self.APP}/Auth/JSON_SendAF2_Code",
                params={"tid": self._pending_tid},
            )
            _raise_for_status(response, "отправка кода 2FA")
            data = response.json()
        except httpx.HTTPError as error:
            raise ServiceUnavailableError(str(error)) from error
        except ValueError as error:
            raise DataParseError(f"ответ отправки кода не JSON: {error}") from error
        destination = (
            data.get("message") or data.get("Message") or data.get("result") or ""
        )
        log.info("БАРС: код 2FA отправлен ({}) | ответ: {}", destination, data)
        # сервер отдаёт HTML (например <br/>) — убираем теги, иначе Telegram
        # не разберёт разметку сообщения
        destination = _strip_html_tags(destination)
        return TwoFASendResult(
            success=bool(data.get("success") or data.get("Success")),
            destination=destination,
        )

    async def verify_2fa_code(self, code: str) -> bool:
        """Проверить код. True — кука auth_bars появилась; False — код неверный."""
        if not self._pending_token or not self._account:
            raise AuthError("нет ожидающего 2FA-состояния")
        try:
            # follow_redirects=True как в master: при успехе сервер отдаёт
            # редирект и ставит auth_bars уже на следующей странице
            response = await self._client.post(
                f"{self.APP}/Auth/LoginCode",
                data={
                    "__RequestVerificationToken": self._pending_token,
                    "StopOpenDefault": "False",
                    "Account": self._account,
                    "RememberMe": "True",
                    "AF2_Code": code,
                },
                follow_redirects=True,
            )
            _raise_for_status(response, "проверка кода 2FA")
            content = response.text
        except httpx.HTTPError as error:
            raise ServiceUnavailableError(str(error)) from error

        # как в master: кука auth_bars либо сессия уже валидна
        if "auth_bars" in self._cookies() or await self.is_authenticated():
            log.info("БАРС: код 2FA принят | account={}", self._account)
            self._pending_token = None
            self._pending_tid = None
            return True

        # код неверный — страница вернула новый токен для повторной попытки
        token = extract_token(content)
        if not token:
            # форма 2FA не в ответе (например, «Доступ запрещён»): повтор со
            # старым токеном бессмыслен — просим начать заново
            log.warning(
                "БАРС: нет формы 2FA для повтора | account={} | cookies={}",
                self._account,
                sorted(self._cookies().keys()),
            )
            self._pending_token = None
            self._pending_tid = None
            raise AuthError(
                "сервер не вернул форму 2FA для повторной попытки — начните сначала"
            )
        self._pending_token = token
        log.info("БАРС: код 2FA неверный | account={}", self._account)
        log.debug(
            "БАРС: ответ LoginCode (фрагмент) | status={} | cookies={} | body={}",
            response.status_code,
            sorted(self._cookies().keys()),
            content[:200].replace("\n", " "),
        )
        return False

    # ───────────────────────────── доступность ─────────────────────────────

    async def check_availability(self) -> bool:
        try:
            response = await self._client.get(f"{self.APP}/", follow_redirects=False)
            return response.status_code < 500
        except httpx.HTTPError:
            return False
