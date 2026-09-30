# BarsWatcher: один образ для bot.py и worker.py
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    TZ=Europe/Moscow

WORKDIR /app

# uv — быстрый менеджер зависимостей
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# сначала зависимости (слоём, до копирования кода)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# код проекта
COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# по умолчанию запускаем бота; worker переопределяет command в compose
CMD ["python", "bot.py"]
