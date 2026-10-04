FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libjpeg-dev \
        zlib1g-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# UV_DEV пусто в проде, "--dev" в dev-сборке (см. docker-compose.dev.yml)
ARG SYNC_ARGS=""

# Слой с зависимостями — кешируется, пока не меняются pyproject.toml / uv.lock
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project $SYNC_ARGS

# Копируем весь проект и доустанавливаем сам пакет
COPY . .
RUN uv sync --frozen $SYNC_ARGS

RUN chmod +x /app/entrypoint.sh

WORKDIR /app/src

EXPOSE 8000
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]