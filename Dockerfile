FROM python:3.14-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y gcc libpq-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*


# Устанавливаем Poetry
RUN pip install "poetry>=2.2.0"

# Копируем файлы проекта
COPY pyproject.toml poetry.lock ./

# Устанавливаем зависимости через Poetry
RUN poetry config virtualenvs.create false \
  && poetry install --no-root --no-interaction --no-ansi

# Копируем остальные файлы проекта
COPY . .

EXPOSE 8000