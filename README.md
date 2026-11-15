# Sport School

CRM спортшколы: спортсмены, семьи и договоры, расписание и посещаемость, соревнования и заявки, документы
с AI-анализом, Telegram-бот, онлайн-трансляция результатов.

**Стек:** Python 3.15 · Django 6.1 · PostgreSQL 18 · Celery 5.7 + Redis 7 · python-telegram-bot 22 ·
Bootstrap 5.3 · Poetry · Docker Compose.

Документация по отдельным подсистемам: [AI-анализ документов](docs/documents-ai.md).

---

## Разработка

```bash
poetry install                     # зависимости, включая dev (pytest, ruff, pre-commit)
poetry run pre-commit install      # ruff check + ruff format на каждый коммит
```

Линтер и форматтер — **ruff** (конфиг в `pyproject.toml`):

```bash
poetry run ruff check .            # линтер
poetry run ruff format .           # форматирование
poetry run pre-commit run --all-files
```

### Тесты

pytest + pytest-django, настройки `config/settings_test.py` (не читают `.env`, не ходят в сеть).
Нужен Postgres. Проще всего гонять в Docker:

```bash
docker network create ss_test
docker run -d --name ss_test_pg --network ss_test \
  -e POSTGRES_DB=sport_school -e POSTGRES_USER=sport_school -e POSTGRES_PASSWORD=sport_school postgres:18
docker build --target test -t sport_school:test .
docker run --rm --network ss_test -e POSTGRES_HOST=ss_test_pg sport_school:test pytest
```

Локально без Docker-образа: поднять Postgres на `localhost:5432` (те же логин/пароль) и `poetry run pytest`.

- `tests/test_smoke.py` — каждая страница под каждой ролью (гость, неподтверждённый, пользователь, тренер,
  менеджер, админ, суперпользователь) открывается без 500.
- `api/tests.py` — онлайн-результаты (вебхук, трансляции, публикация в Telegram).

### Docker Compose

Сервисы: `postgres` (18), `redis` (7), `migrate` (одноразовый: проверка БД → миграции → collectstatic),
`web` (gunicorn), `bot`, `celery_worker_docs`, `celery_worker_default` (со встроенным beat).

Нужны `.env` (см. `.env.sample`) и `.env.postgres` (см. `.env.postgres.example`).

```bash
docker compose up -d --build
```

На новой пустой БД `migrate` остановится с ошибкой — это защита от старта прода на пустой базе.
Для первичной инициализации: `ALLOW_EMPTY_DB=True docker compose run --rm migrate`.

---

## План работ

Отмечаем по мере выполнения. Решения: CI не усложняем; Poetry остаётся; Postgres — сервисом в compose;
Redis остаётся на 7; Tailwind пробуем на отдельной странице.

### Этап 0. Фундамент

- [x] pytest-django, factory_boy, `config/settings_test.py`; в тестах запрещён выход в сеть
- [x] Smoke-тесты: все страницы × 7 ролей (452 теста). Нашли и исправили 500 на `classes/<pk>/notify/`
- [ ] Тесты ключевых сервисов: договоры и финансы, расписание, дедупликация людей, привязка документов
- [x] ruff вместо flake8 + black + isort, код отформатирован
- [x] pre-commit (ruff, проверки yaml/toml, merge-конфликты, крупные файлы, приватные ключи)
- [x] Redis без публикации порта наружу
- [x] Общий кэш Django на Redis (раньше LocMemCache: блокировки и cooldown не работали между процессами)
- [x] `ALLOWED_HOSTS` из env (по умолчанию — хост `SITE_BASE_URL` + www, хост вебхука, localhost)
- [x] `.gitignore` / `.dockerignore`: дампы, логи, статика, celerybeat, локальные env
- [x] Проверка `"test" in sys.argv` в коде публикации заменена на настройку `ONLINE_RESULTS_TELEGRAM_PUBLISH_SYNC`

### Этап 1. Обновления

- [x] Все пакеты до последних версий (Django 6.1.3, Celery 5.7, DRF 3.18, PTB 22.8, Pillow 13, gunicorn 27, redis-py 8 …)
- [x] Django 6.1: устранены deprecation-предупреждения (почта переведена на `MAILERS`)
- [x] psycopg2 → psycopg 3; `CONN_MAX_AGE` + health checks; встроенный пул — флагом `DB_POOL=True`
- [x] Python 3.14 → 3.15 (колёса всех зависимостей есть для amd64 и arm64)
- [x] Dockerfile: multi-stage, без root (uid 1000), poetry только на этапе сборки
- [x] Compose: HTTP healthcheck `/healthz/`, без монтирования `.:/app`, миграции и collectstatic отдельным шагом
- [x] Celery: убран неиспользуемый result backend, beat встроен в default-воркер
- [x] Миграции на чистой БД проходят целиком, `makemigrations --check` чистый
- [x] Postgres 18 сервисом в compose + скрипт переезда `scripts/pg_upgrade_to_18.sh` (проверен локально)
- [ ] **Переезд на сервере 17 → 18** — см. «Деплой этой ветки» ниже
- [ ] Staging: второй compose-проект на сервере с копией базы

### Этап 2. `results` — новый сервис онлайн-результатов (вместо online_results_script)

- [ ] Фикстуры реальных протоколов + эталонные результаты (golden-тесты)
- [ ] Модели: `ResultsEvent`, `Source`, `Group`, `Entry` (FK → `school.Athlete`), `RunResult` (мс + статус), `EventLog`, `RawSnapshot`
- [ ] `parsing/`: адаптеры источников (Google Sheets, XLSX, загрузка файла, ручной ввод), разметка колонок в БД, dry-run
- [ ] `engine/`: чистые функции `state + now → места, фаза, фокус, прогноз` — единственное место логики заездов
- [ ] Воркер (management command + сервис в compose): подхват активных трансляций после рестарта, адаптивный интервал опроса
- [ ] Запись `RawSnapshot` и команда `results_replay` для офлайн-воспроизведения трансляции
- [ ] Веб: live-страница и админка трансляции (старт/стоп, предпросмотр, ручные правки)
- [ ] Telegram: закреплённое live-сообщение, итоги групп, сводка; очередь с лимитами
- [ ] Связка участников протокола со спортсменами школы
- [ ] 1–2 соревнования параллельно со старым сервисом, сравнение
- [ ] Удаление онлайн-результатов из `api/`, архив online_results_script

### Этап 3. Дизайн

- [ ] Пробная страница на Tailwind (CSS-сборка без Node — standalone CLI или аналог)
- [ ] Решение: Tailwind или Bootstrap с собственной темой
- [ ] Дизайн-система: токены, компоненты шаблонов (django-cotton / django-components), тёмная тема, mobile-first
- [ ] HTMX + Alpine.js вместо инлайн-скриптов (23 шаблона)
- [ ] Live-страница результатов — изолированный «остров» (Preact/Svelte + Vite)
- [ ] Telegram Mini App для родителей и тренеров (расписание, посещаемость, договоры, результаты)
- [ ] Редизайн остальных разделов по одному

### Этап 4. Развитие

- [ ] Профиль спортсмена: история стартов, места, график прогресса
- [ ] Автоматическая проверка разрядных нормативов по результатам
- [ ] Заявки на соревнования в форматах федерации (docx/xlsx)
- [ ] Онлайн-оплата по договорам (ЮKassa / СБП), напоминания о долгах
- [ ] Напоминания об истечении медсправок и страховок
- [ ] Аналитика посещаемости, сигнал «пропал на 3 недели»
- [ ] ICS-подписка на расписание
- [ ] Дашборд руководителя: посещаемость, выручка, отток, загрузка групп
- [ ] Единый центр уведомлений (бот / почта / WhatsApp) с настройками каналов
- [ ] Аудит-лог изменений по договорам и финансам

### Бэклог

- [ ] Renovate или Dependabot
- [ ] CI: lint + тесты на PR, деплой только после зелёных тестов
- [x] Деплой на сервере через systemd и Git по SSH, без `docker compose down`
- [ ] Sentry / GlitchTip для ошибок (облачный Sentry может быть недоступен из РФ)
- [ ] Бэкапы Postgres по расписанию (например, `pg_dump` в Object Storage)
- [ ] Squash старых миграций `school` (сейчас проходят, но их 50)
- [ ] ruff: включить `DJ001` (null у строковых полей) и `DJ012` (порядок в моделях) — требуют миграций/перестановок
- [ ] Разрезать толстые модули: `school/views.py`, `members/views.py`, `tg_bot/handlers/manager.py`, `tg_bot/handlers/user.py`

---

## Деплой этой ветки (переезд на Postgres 18)

На продакшене таймер systemd проверяет `origin/dev` раз в минуту. Деплой выполняется на
`home-server-2`, куда GitHub-hosted runner не может подключиться по локальному адресу.
GitHub Actions отдельно запускает тесты с PostgreSQL 18.

На сервере от пользователя `kb`:

```bash
install -D -m 0755 scripts/deploy_dev.sh ~/.local/bin/sport-school-deploy
install -D -m 0644 deploy/systemd/sport-school-deploy.service ~/.config/systemd/user/sport-school-deploy.service
install -D -m 0644 deploy/systemd/sport-school-deploy.timer ~/.config/systemd/user/sport-school-deploy.timer
sudo loginctl enable-linger kb
systemctl --user daemon-reload
systemctl --user enable --now sport-school-deploy.timer
journalctl --user -u sport-school-deploy.service -n 30
```

Скрипт проверяет, что рабочая база PostgreSQL 18 уже инициализирована, прежде чем обновлять
код и контейнеры. Успешно развёрнутый коммит сохраняется в
`~/.local/state/sport-school/deployed-dev`.

**До мержа**, на сервере в `/srv/projects/sport_school`:

```bash
cp docker-compose.yml ~/sport_school-compose-pg17.yml   # копия для отката
git checkout -- docker-compose.yml                       # локальные правки теперь в репозитории
mv .dockerignore ~/sport_school-dockerignore.bak         # файл теперь в репозитории, иначе git pull упадёт
```

Проверить `.env`:
- старые `NAME/USER/PASSWORD/HOST/PORT` продолжают работать; можно перейти на `POSTGRES_*`;
- `CELERY_RESULT_BACKEND` больше не используется;
- при необходимости задать `ALLOWED_HOSTS` явно (по умолчанию: `ksclub.ru`, `www.ksclub.ru`, хост вебхука, localhost).

**Для первого перехода на PostgreSQL 18** на сервере:

```bash
cd /srv/projects/sport_school
git pull --ff-only origin dev
./scripts/pg_upgrade_to_18.sh
curl -s http://127.0.0.1:8010/healthz/    # {"status": "ok"}
```

Скрипт останавливает приложение, снимает дамп со старого тома (`backups/`), восстанавливает его в Postgres 18,
сверяет число строк, выставляет права на `logs/` и `staticfiles/` (uid 1000) и поднимает стек.
Старый том `sport_school_postgres_data` не удаляется.

**Откат:** вернуть старый compose (`cp ~/sport_school-compose-pg17.yml docker-compose.yml`), откатить код на
предыдущий коммит `dev` и `docker compose up -d --build --remove-orphans` — старый том с Postgres 17 нетронут.
