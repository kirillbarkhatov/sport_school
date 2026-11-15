#!/usr/bin/env bash
# Проверка конфигураций Compose: прод (docker-compose.yml) и staging (+ docker-compose.staging.yml).
# Рендерит обе на фиктивных .env во временном каталоге и проверяет ключевые свойства staging:
# свой тег образа, только порт 8020, бот выключен, воркер без beat. Без docker — ошибка
# (пропуск только явно: SKIP_COMPOSE_CHECK=1).
set -euo pipefail
[ "${SKIP_COMPOSE_CHECK:-0}" = "1" ] && { echo "compose-config: пропущено (SKIP_COMPOSE_CHECK=1)"; exit 0; }
docker compose version >/dev/null 2>&1 || { echo "compose-config: нужен docker compose" >&2; exit 1; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
cp "$ROOT/docker-compose.yml" "$ROOT/docker-compose.staging.yml" "$TMP/"
printf 'STAGING_MODE=True\nWEB_BIND=127.0.0.1:8020\n' > "$TMP/.env"
printf 'POSTGRES_DB=x\nPOSTGRES_USER=x\nPOSTGRES_PASSWORD=x\n' > "$TMP/.env.postgres"
cd "$TMP"
docker compose -p sport_school -f docker-compose.yml config -q
docker compose -p sport_school_staging -f docker-compose.yml -f docker-compose.staging.yml config --format json > staging.json
python3 - <<'PY'
import json, sys
c = json.load(open("staging.json"))
errors = []
app = ["migrate", "web", "celery_worker_docs", "celery_worker_default", "results_worker"]
for name in app:
    s = c["services"][name]
    if s.get("image") != "sport_school:staging":
        errors.append(f"{name}: образ {s.get('image')}, ожидался sport_school:staging")
    if s.get("environment", {}).get("STAGING_MODE") != "True":
        errors.append(f"{name}: нет STAGING_MODE=True")
ports = [(p.get("host_ip"), str(p.get("published"))) for s in c["services"].values() for p in s.get("ports", [])]
if ports != [("127.0.0.1", "8020")]:
    errors.append(f"порты staging: {ports}, ожидался только 127.0.0.1:8020")
if "bot" in c["services"]:
    errors.append("бот входит в запускаемые сервисы staging")
if "-B" in c["services"]["celery_worker_default"].get("command", []):
    errors.append("celery_worker_default на staging с -B")
if errors:
    print("compose-config:\n  " + "\n  ".join(errors), file=sys.stderr)
    sys.exit(1)
print("compose-config: прод и staging в порядке")
PY
