#!/usr/bin/env bash
# docker compose для staging: всегда проект sport_school_staging и override-файл staging.
# Запускать из каталога staging (/srv/projects/sport_school_staging). Отказывается работать в каталоге прода.
set -euo pipefail
STAGING_PROJECT="${STAGING_PROJECT:-sport_school_staging}"
case "$STAGING_PROJECT" in *_staging) ;; *) echo "STAGING_PROJECT должен оканчиваться на _staging" >&2; exit 2 ;; esac
grep -qE '^STAGING_MODE=True' .env 2>/dev/null || { echo "В ./.env нет STAGING_MODE=True — это не каталог staging" >&2; exit 2; }
exec docker compose -p "$STAGING_PROJECT" -f docker-compose.yml -f docker-compose.staging.yml "$@"
