#!/usr/bin/env bash
# Бэкап базы compose-проекта: backups/<проект>_<дата>.dump (custom-формат pg_dump), хранятся последние 14.
#   ./scripts/backup_db.sh                    # проект = имя каталога (на проде — sport_school)
#   COMPOSE_PROJECT_NAME=sport_school_staging ./scripts/backup_db.sh
# Дамп проверяется (pg_restore --list) до того, как старые бэкапы удаляются.
set -euo pipefail
umask 077  # дамп и каталог доступны только владельцу, независимо от umask вызывающего shell
PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"
KEEP="${BACKUP_KEEP:-14}"
DIR="${BACKUP_DIR:-backups}"
[ -f .env.postgres ] || { echo "Нет .env.postgres" >&2; exit 1; }
set -a; . ./.env.postgres; set +a
: "${POSTGRES_USER:?}" "${POSTGRES_DB:?}"
CONTAINER="${PROJECT}-postgres-1"
docker inspect "$CONTAINER" >/dev/null 2>&1 || { echo "Контейнер $CONTAINER не найден" >&2; exit 1; }

mkdir -p "$DIR"
chmod 700 "$DIR"
FILE="$DIR/${PROJECT}_$(date +%Y%m%d_%H%M%S).dump"
docker exec "$CONTAINER" pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$FILE.partial"
[ -s "$FILE.partial" ] || { rm -f "$FILE.partial"; echo "Пустой дамп" >&2; exit 1; }
docker exec -i "$CONTAINER" pg_restore --list < "$FILE.partial" > /dev/null || { echo "Дамп не читается" >&2; exit 1; }
chmod 600 "$FILE.partial"
mv "$FILE.partial" "$FILE"
ls -1t "$DIR/${PROJECT}_"*.dump | tail -n +$((KEEP + 1)) | while read -r old; do rm -f -- "$old"; done
echo "OK $FILE $(du -h "$FILE" | cut -f1)"
