# syntax=docker/dockerfile:1
ARG PYTHON_VERSION=3.15

# --- Сборка зависимостей: poetry ставит пакеты в /opt/venv, сам poetry в рантайм не попадает
FROM python:${PYTHON_VERSION}-slim AS builder
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POETRY_VERSION=2.3.2 \
    POETRY_NO_INTERACTION=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
RUN pip install "poetry==${POETRY_VERSION}" && python -m venv /opt/venv
WORKDIR /build
COPY pyproject.toml poetry.lock ./
RUN poetry install --no-root --only main

FROM builder AS builder-dev
RUN poetry install --no-root --only main,dev

# --- Общая база рантайма
FROM python:${PYTHON_VERSION}-slim AS base
ARG APP_UID=1000
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
RUN groupadd --gid ${APP_UID} app && useradd --uid ${APP_UID} --gid app --create-home app
WORKDIR /app
RUN mkdir -p logs staticfiles media && chown -R app:app /app

# --- Образ для тестов и линтера: docker build --target test -t sport_school:test .
FROM base AS test
COPY --from=builder-dev /opt/venv /opt/venv
COPY --chown=app:app . .
USER app
CMD ["pytest"]

# --- Рантайм (цель по умолчанию)
FROM base AS runtime
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app . .
USER app
EXPOSE 8000
CMD ["/opt/venv/bin/gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
