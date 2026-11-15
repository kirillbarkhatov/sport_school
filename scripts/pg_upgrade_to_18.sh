#!/usr/bin/env bash
# Переезд данных со старого тома Postgres 17 (postgres_data) на новый том Postgres 18 (postgres18_data).
#
# Запускать из корня проекта на сервере ПОСЛЕ git pull ветки с новым docker-compose.yml:
#   ./scripts/pg_upgrade_to_18.sh
#
# Что делает:
#   1. Останавливает приложение (web, bot, celery) и старый Postgres.
#   2. Поднимает временный Postgres 17 на старом томе и снимает дамп (backups/pg17_<дата>.dump).
#   3. Поднимает новый Postgres 18 (пустой том postgres18_data) и восстанавливает дамп.
#   4. Сверяет число строк в ключевых таблицах.
#   5. Выставляет права на logs/ и staticfiles/ для пользователя контейнера (uid 1000).
#   6. Поднимает весь стек.
# Старый том не удаляется: откат — вернуть старый docker-compose.yml (git checkout) и docker compose up -d.
set -euo pipefail

PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"
OLD_VOLUME="${OLD_VOLUME:-${PROJECT}_postgres_data}"
OLD_IMAGE="${OLD_IMAGE:-postgres:17.6-bookworm}"
APP_UID="${APP_UID:-1000}"
TMP_CONTAINER="${PROJECT}_pg17_upgrade_tmp"
BACKUP_DIR="backups"
DUMP_FILE="${BACKUP_DIR}/pg17_$(date +%Y%m%d_%H%M%S).dump"
CHECK_TABLES="django_migrations users_user school_person school_athlete school_family school_class"

[ -f .env.postgres ] || { echo "Нет .env.postgres" >&2; exit 1; }
set -a; . ./.env.postgres; set +a
: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}"

docker volume inspect "$OLD_VOLUME" >/dev/null 2>&1 || { echo "Старый том $OLD_VOLUME не найден" >&2; exit 1; }

compose() { docker compose -p "$PROJECT" "$@"; }

count_rows() {  # $1 — контейнер
  for t in $CHECK_TABLES; do
    printf '%s=' "$t"
    docker exec "$1" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "SELECT count(*) FROM $t" 2>/dev/null || echo "нет"
  done
}

wait_ready() {  # $1 — контейнер
  for _ in $(seq 1 60); do
    docker exec "$1" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1 && return 0
    sleep 1
  done
  echo "Postgres в $1 не поднялся" >&2; exit 1
}

echo "==> 1. Останавливаю приложение и старый Postgres (проект $PROJECT)"
compose stop web bot celery_worker_default celery_worker_docs migrate 2>/dev/null || true
docker stop "${PROJECT}-celery_beat-1" 2>/dev/null || true
# Старый контейнер Postgres удаляем (том остаётся), чтобы compose создал новый на postgres18_data
docker rm -f "${PROJECT}-postgres-1" 2>/dev/null || true

echo "==> 2. Дамп со старого тома $OLD_VOLUME ($OLD_IMAGE)"
mkdir -p "$BACKUP_DIR"
docker rm -f "$TMP_CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$TMP_CONTAINER" -v "$OLD_VOLUME":/var/lib/postgresql/data "$OLD_IMAGE" >/dev/null
trap 'docker rm -f "$TMP_CONTAINER" >/dev/null 2>&1 || true' EXIT
wait_ready "$TMP_CONTAINER"
OLD_COUNTS="$(count_rows "$TMP_CONTAINER")"
docker exec "$TMP_CONTAINER" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$DUMP_FILE"
docker rm -f "$TMP_CONTAINER" >/dev/null
echo "    дамп: $DUMP_FILE ($(du -h "$DUMP_FILE" | cut -f1))"

echo "==> 3. Новый Postgres 18 и восстановление"
compose up -d postgres
NEW_CONTAINER="$(compose ps -q postgres)"
wait_ready "$NEW_CONTAINER"
EXISTING="$(docker exec "$NEW_CONTAINER" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "SELECT count(*) FROM information_schema.tables WHERE table_name='django_migrations'")"
if [ "$EXISTING" != "0" ]; then
  echo "В новой БД уже есть данные — восстановление пропущено (проверьте вручную)." >&2; exit 1
fi
docker exec -i "$NEW_CONTAINER" pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error < "$DUMP_FILE"

echo "==> 4. Сверка числа строк"
NEW_COUNTS="$(count_rows "$NEW_CONTAINER")"
echo "    было:  $(echo $OLD_COUNTS)"
echo "    стало: $(echo $NEW_COUNTS)"
if [ "$OLD_COUNTS" != "$NEW_COUNTS" ]; then
  echo "Число строк не совпадает — стек НЕ поднимаю. Разберитесь вручную." >&2; exit 1
fi

echo "==> 5. Права на logs/ и staticfiles/ (uid $APP_UID)"
mkdir -p logs staticfiles
docker run --rm -v "$PWD":/w alpine chown -R "$APP_UID:$APP_UID" /w/logs /w/staticfiles

echo "==> 6. Поднимаю стек"
compose up -d --build --remove-orphans
compose ps
echo "Готово. Старый том $OLD_VOLUME сохранён; удалить после проверки: docker volume rm $OLD_VOLUME"
