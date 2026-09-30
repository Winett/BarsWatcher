"""Логирование HTTP-запросов интеграций (для анализа).

Каждый запрос/ответ пишется одной строкой:
    http | БАРС | → GET https://bars.mpei.ru/bars_web/...
    http | БАРС | ← GET ... 200 (120ms)
"""

from __future__ import annotations

import time

import httpx
from loguru import logger

__all__ = ["attach_http_logging"]

log = logger.bind(component="http")


def attach_http_logging(client: httpx.AsyncClient, tag: str) -> None:
    """Повесить логирование запросов/ответов на httpx-клиент."""

    async def on_request(request: httpx.Request) -> None:
        request.extensions["started_at"] = time.monotonic()
        log.debug("{} | → {} {}", tag, request.method, request.url)

    async def on_response(response: httpx.Response) -> None:
        request = response.request
        started = request.extensions.get("started_at")
        elapsed = (
            f"{int((time.monotonic() - started) * 1000)}ms" if started else "?"
        )
        log.info(
            "{} | ← {} {} → {} ({})",
            tag,
            request.method,
            request.url,
            response.status_code,
            elapsed,
        )

    client.event_hooks["request"].append(on_request)
    client.event_hooks["response"].append(on_response)
