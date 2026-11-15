#!/usr/bin/env bash
# Обновить staging свежей копией базы прода.
#   cd /srv/projects/sport_school_staging && ./scripts/staging_refresh.sh [--anonymize] [--dry-run]
# Защита от перепутывания баз: работает только из каталога staging (STAGING_MODE=True в .env, порт не 8010,
# проект *_staging, каталог не совпадает с каталогом прода) и пишет только в контейнер <проект_staging>-postgres-1.
set -euo pipefail
SOURCE_PROJECT="${SOURCE_PROJECT:-sport_school}"
STAGING_PROJECT="${STAGING_PROJECT:-sport_school_staging}"
ANONYMIZE=0; DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --anonymize) ANONYMIZE=1 ;;
    --dry-run) DRY_RUN=1 ;;
    *) echo "Неизвестный параметр: $arg" >&2; exit 2 ;;
  esac
done
fail() { echo "ОТКАЗ: $*" >&2; exit 3; }

# --- Проверки (до любых действий) ---
case "$STAGING_PROJECT" in *_staging) ;; *) fail "проект staging должен оканчиваться на _staging ($STAGING_PROJECT)" ;; esac
[ "$STAGING_PROJECT" != "$SOURCE_PROJECT" ] || fail "проект staging совпадает с проектом прода"
[ -f .env ] || fail "нет ./.env — запускайте из каталога staging"
grep -qE '^STAGING_MODE=True' .env || fail "в ./.env нет STAGING_MODE=True"
grep -qE '^WEB_BIND=.*:8010$' .env && fail "в ./.env WEB_BIND указывает на порт прода 8010"
[ -f .env.postgres ] || fail "нет ./.env.postgres"
SOURCE_DIR="${SOURCE_DIR:-/srv/projects/$SOURCE_PROJECT}"
[ "$(cd "$PWD" && pwd -P)" != "$(cd "$SOURCE_DIR" 2>/dev/null && pwd -P || echo /nonexistent)" ] || fail "текущий каталог — каталог прода"
set -a; . ./.env.postgres; set +a
: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}"
TARGET="${STAGING_PROJECT}-postgres-1"
SOURCE="${SOURCE_PROJECT}-postgres-1"

echo "План: $SOURCE → $TARGET (база $POSTGRES_DB), обезличивание: $ANONYMIZE"
if [ "$DRY_RUN" = 1 ]; then echo "DRY-RUN: проверки пройдены, действия не выполнялись"; exit 0; fi

compose() { STAGING_PROJECT="$STAGING_PROJECT" ./scripts/staging_compose.sh "$@"; }

# --- Действия ---
docker inspect "$SOURCE" >/dev/null 2>&1 || fail "контейнер прода $SOURCE не найден"
compose stop web results_worker celery_worker_default celery_worker_docs 2>/dev/null || true
compose up -d postgres redis
for _ in $(seq 1 60); do docker exec "$TARGET" pg_isready -U "$POSTGRES_USER" >/dev/null 2>&1 && break; sleep 1; done
[ "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$TARGET")" = "$STAGING_PROJECT" ] \
  || fail "контейнер $TARGET не принадлежит проекту $STAGING_PROJECT"

SRC_USER="$(docker exec "$SOURCE" printenv POSTGRES_USER)"
SRC_DB="$(docker exec "$SOURCE" printenv POSTGRES_DB)"
DUMP="$(mktemp -t staging_refresh.XXXXXX)"
trap 'rm -f "$DUMP"' EXIT
docker exec "$SOURCE" pg_dump -U "$SRC_USER" -d "$SRC_DB" -Fc > "$DUMP"
[ -s "$DUMP" ] || fail "пустой дамп прода"

docker exec "$TARGET" psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 \
  -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" -c "CREATE DATABASE \"$POSTGRES_DB\""
docker exec -i "$TARGET" pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges < "$DUMP"

if [ "$ANONYMIZE" = 1 ]; then
  docker exec -i "$TARGET" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 < scripts/sql/staging_anonymize.sql
fi

compose run --rm migrate
compose up -d --build --remove-orphans
echo "Готово. Пользователей: $(docker exec "$TARGET" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc 'select count(*) from users_user')"
