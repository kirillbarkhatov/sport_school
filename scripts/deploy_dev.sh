#!/usr/bin/env bash
set -euo pipefail
umask 077

expected="${1:-}"
if [[ ! "$expected" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Usage: deploy_dev.sh <tested-commit-sha>" >&2
  exit 1
fi

cd /srv/projects/sport_school
exec 9>"$HOME/.sport-school-deploy.lock"
flock -w 300 9

export GIT_TERMINAL_PROMPT=0
export GIT_SSH_COMMAND='ssh -o BatchMode=yes'
git fetch --quiet origin dev
target="$(git rev-parse origin/dev)"
if [ "$expected" != "$target" ]; then
  printf 'Skipping %s because dev is now %s\n' "$expected" "$target"
  exit 0
fi
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/sport-school"
state_file="$state_dir/deployed-dev"
if [ -f "$state_file" ] && [ "$target" = "$(cat "$state_file")" ]; then
  exit 0
fi

if ! docker exec sport_school-postgres-1 sh -ec '
  psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
    "SELECT current_setting('\''server_version_num'\'')::int >= 180000 AND EXISTS (SELECT 1 FROM django_migrations)" |
  grep -qx t
'; then
  echo "Production requires an initialized PostgreSQL 18 database." >&2
  exit 1
fi

git diff --quiet
git diff --cached --quiet
git merge --ff-only "$target"
test "$(git rev-parse HEAD)" = "$target"
docker compose config --quiet
docker compose build web
docker compose up -d --no-build --remove-orphans --wait --wait-timeout 180
curl --fail --silent --show-error --max-time 10 \
  --resolve ksclub.ru:443:127.0.0.1 https://ksclub.ru/healthz/
docker compose ps

mkdir -p "$state_dir"
printf '%s\n' "$target" > "$state_file.tmp"
mv "$state_file.tmp" "$state_file"
printf 'Deployment verified: %s\n' "$target"
