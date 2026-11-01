"""Авторизация в ОСЭП (OWA, Exchange 2013).

Forms-based auth: POST /CookieAuth.dll?Logon (форма curl/flags/formdir),
успех — GET /owa/ со статусом 200 (неавторизованному отдаётся 302 на логин).
2FA в ОСЭП нет. Канарейка X-OWA-CANARY живёт в куке того же имени и
передаётся заголовком X-Owa-Canary во все API-запросы.
"""

from __future__ import annotations

import asyncio

import httpx
from loguru import logger

from src.integrations.ports import (
    AuthError,
    AuthResult,
    AuthStatus,
    DataParseError,
    ServiceUnavailableError,
)

from ..httplog import attach_http_logging

BASE_URL = "https://mail.mpei.ru"
OWA_PATH = "/owa/"
LOGON_PATH = "/CookieAuth.dll?Logon"
HEALTH_PATH = "/CookieAuth.dll?GetLogon?curl=Z2FowaZ2FZ3FbOZ3D1&reason=0&formdir=2"

# так OWA-клиент представляется серверу (как в master)
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 "
    "OPR/125.0.0.0 (Edition Yx GX)"
)

# форма входа CookieAuth.dll?Logon (Exchange 2013 forms-based auth)
_LOGON_FORM = {
    "curl": "Z2FowaZ2F",
    "flags": "0",
    "forcedownlevel": "0",
    "formdir": "2",
    "isUtf8": "1",
    "trusted": "4",
}

AUTH_ATTEMPTS = 2  # попытки входа: если сервер «заглючил» — пробуем ещё раз
AUTH_ATTEMPT_TIMEOUT = 40.0  # жёсткий потолок одной попытки (сек)


class OwaAuth:
    """Сессия OWA: вход по логину/паролю, канарейка, экспорт/восстановление."""

    def __init__(self, timeout: float = 30.0):
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=timeout,
            follow_redirects=False,
            verify=False,  # как в master: TLS корпоративной почты не проверяется
            headers={"User-Agent": USER_AGENT},
        )
        attach_http_logging(self._client, "ОСЭП")
        self._credentials: tuple[str, str] | None = None
        self._account: str | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    @property
    def canary(self) -> str | None:
        """X-OWA-CANARY из куки (None — сессия ещё не логинилась).

        OWA ставит кук несколько раз (разные пути/домены), и httpx
        cookies.get() в этом случае падает с CookieConflict — поэтому читаем
        jar напрямую и берём последний (самый свежий).
        """
        values = [
            cookie.value
            for cookie in self._client.cookies.jar
            if cookie.name == "X-OWA-CANARY"
        ]
        return values[-1] if values else None

    async def is_authenticated(self) -> bool:
        """200 на /owa/ — сессия жива; 302 — ушли на форму входа."""
        try:
            response = await self._client.get(OWA_PATH)
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(f"ОСЭП: {exc!r}") from exc
        return response.status_code == 200

    async def authenticate(self, login: str, password: str) -> AuthResult:
        """Вход в ОСЭП. Неверные креды = /owa/ снова отдаёт 302 после Logon.

        Потолок одной попытки — AUTH_ATTEMPT_TIMEOUT (не даём зависнуть на
        «Попытка авторизоваться»). Если сервис доступен, но попытка не удалась
        (timeout/5xx) — пробуем ещё раз: мог сбоить сам сервис.
        """
        if not login or not password:
            raise AuthError(
                f"пустые креды: login={login!r} (длина {len(login)}), "
                f"пароль задан: {bool(password)}"
            )
        self._credentials = (login, password)
        self._account = login

        last_error: Exception | None = None
        for attempt in range(1, AUTH_ATTEMPTS + 1):
            logger.info(
                "ОСЭП: вход, account={} (попытка {}/{})",
                login, attempt, AUTH_ATTEMPTS,
            )
            try:
                result = await asyncio.wait_for(
                    self._attempt_login(login, password),
                    timeout=AUTH_ATTEMPT_TIMEOUT,
                )
            except (ServiceUnavailableError, AuthError, DataParseError) as error:
                # сервер сбоит / форма не та — это НЕ «неверные креды»
                last_error = error
                logger.warning(
                    "ОСЭП: попытка {} не удалась ({})", attempt, error
                )
                continue
            if result.status is not AuthStatus.OK:
                # сервис ответил и отклонил креды — ретраи не помогут
                return result
            return result

        # все попытки упали на уровне сервиса — не виним креды
        assert last_error is not None
        raise ServiceUnavailableError(
            f"ОСЭП: вход не удался после {AUTH_ATTEMPTS} попыток: {last_error}"
        ) from last_error

    async def _attempt_login(self, login: str, password: str) -> AuthResult:
        """Одна попытка входа (без ретраев): warm-up + Logon + проверка /owa/."""
        self._client.cookies.clear()
        try:
            # warm-up кук + формы
            await self._client.get(OWA_PATH)
            response = await self._client.post(
                LOGON_PATH,
                data={**_LOGON_FORM, "username": login, "password": password},
            )
        except httpx.HTTPError as exc:
            raise ServiceUnavailableError(f"ОСЭП: вход невозможен, {exc!r}") from exc

        if response.status_code >= 500 or response.status_code == 429:
            raise ServiceUnavailableError(
                f"ОСЭП: сервер входа недоступен (HTTP {response.status_code})"
            )

        if await self.is_authenticated():
            logger.info("ОСЭП: вход выполнен (account={})", login)
            return AuthResult(status=AuthStatus.OK)

        logger.warning(
            "ОСЭП: вход не удался — /owa/ снова отдаёт редирект (HTTP {})",
            response.status_code,
        )
        return AuthResult(
            status=AuthStatus.INVALID_CREDENTIALS,
            error="форма входа снова показана (логин/пароль не приняты)",
        )

    async def relogin(self) -> None:
        """Перелогин при истёкшей сессии (302/401 на API-запросе)."""
        if self._credentials is None:
            raise AuthError("сессия слетела, креды недоступны")
        login, password = self._credentials
        result = await self.authenticate(login, password)
        if result.status is not AuthStatus.OK:
            raise AuthError("перелогин в ОСЭП не восстановил сессию")

    def can_relogin(self) -> bool:
        return self._credentials is not None

    async def check_availability(self) -> bool:
        """<500 на health-URL — сервис жив (для дежурного мониторинга)."""
        try:
            response = await self._client.get(HEALTH_PATH)
        except httpx.HTTPError as exc:
            logger.warning("ОСЭП: health-check не прошёл: {}", exc)
            return False
        return response.status_code < 500

    # ─────────────────────── сессии (export/restore) ───────────────────────

    def export_session(self) -> dict:
        """{"cookies": {...}, "pending_2fa": None} — формат сохранения сессии.

        Собираем из jar (а не dict(cookies)): при нескольких одноимённых куках
        OWA httpx кидает CookieConflict — берём последнюю свежую.
        """
        cookies: dict[str, str] = {}
        for cookie in self._client.cookies.jar:
            cookies[cookie.name] = cookie.value
        return {
            "cookies": cookies,
            "pending_2fa": None,  # 2FA в ОСЭП нет
        }

    def restore_session(self, session: dict | None) -> None:
        if not session:
            return
        # сессия восстанавливается целиком: убираем текущие куки, чтобы
        # повторный restore не плодил дубли одноимённых (X-OWA-CANARY и пр.)
        self._client.cookies.clear()
        for name, value in (session.get("cookies") or {}).items():
            self._client.cookies.set(name, value)
        if self._account is None:
            self._account = "сессия восстановлена"
        logger.info("ОСЭП: сессия восстановлена из сохранённого состояния")
