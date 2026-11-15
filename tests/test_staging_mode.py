"""STAGING_MODE: на staging выключены все внешние каналы. Тест на каждый канал + защиты скриптов."""

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import override_settings

ROOT = Path(__file__).resolve().parent.parent


def _settings_with(env: dict) -> dict:
    """Импортировать настройки в отдельном процессе с заданным окружением и вернуть значения каналов."""
    code = (
        "import json; import config.settings as s;"
        "print(json.dumps({"
        "'bot': s.BOT_TOKEN, 'storage': s.STORAGES['default']['BACKEND'],"
        "'mail': s.MAILERS['default']['BACKEND'], 'beat': list(s.CELERY_BEAT_SCHEDULE),"
        "'assistant': s.AI_ASSISTANT_ENABLED, 'orchestrator': s.DOCS_AI_ORCHESTRATOR_ENABLED,"
        "'docs_sync': s.DOCS_SYNC_ON_STARTUP, 'tg_import': s.TELEGRAM_DOC_IMPORT_ENABLED,"
        "'tg_log_handler': 'telegram' in s.LOGGING['handlers'],"
        "'legacy_launch': s.LEGACY_ONLINE_RESULTS_LAUNCH_ENABLED, 'staging': s.STAGING_MODE}))"
    )
    base = {
        "PATH": os.environ.get("PATH", ""),
        "ENV_FILE": "",
        "SECRET_KEY": "x" * 50,
        "SITE_BASE_URL": "https://staging.example.org",
        # Боевые значения, которые могли попасть в .env staging по ошибке
        "BOT_TOKEN": "123:REAL",
        "TELEGRAM_LOG_CHAT_ID": "1",
        "STORAGE_BACKEND": "s3",
        "AWS_ACCESS_KEY_ID": "a",
        "AWS_SECRET_ACCESS_KEY": "b",
        "AWS_STORAGE_BUCKET_NAME": "c",
        "AI_ASSISTANT_ENABLED": "True",
        "DOCS_AI_ORCHESTRATOR_ENABLED": "True",
        "DOCS_SYNC_ON_STARTUP": "True",
        "TELEGRAM_DOC_IMPORT_ENABLED": "True",
        "LEGACY_ONLINE_RESULTS_LAUNCH_ENABLED": "True",
    }
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env={**base, **env}, capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_staging_mode_disables_every_channel_in_settings():
    values = _settings_with({"STAGING_MODE": "True"})
    assert values["staging"] is True
    assert values["bot"] == ""  # Telegram: бот, уведомления, публикации, лог-хэндлер
    assert values["tg_log_handler"] is False
    assert values["storage"] == "django.core.files.storage.FileSystemStorage"  # не S3
    assert values["mail"] == "django.core.mail.backends.console.EmailBackend"
    assert values["beat"] == []  # расписание Celery пустое
    assert values["assistant"] is False and values["orchestrator"] is False
    assert values["docs_sync"] is False and values["tg_import"] is False
    assert values["legacy_launch"] is False


def test_without_staging_mode_production_values_are_kept():
    values = _settings_with({"STAGING_MODE": "False"})
    assert values["bot"] == "123:REAL" and values["tg_log_handler"] is True
    assert values["storage"].endswith("S3Boto3Storage") and values["beat"]


@override_settings(STAGING_MODE=True, AI_ASSISTANT_ENABLED=True, AI_ASSISTANT_BASE_URL="https://ai.example.org")
def test_assistant_client_refuses_on_staging():
    from assistant.client import AssistantClient, AssistantIntegrationDisabled

    with patch("assistant.client.requests.post") as post, pytest.raises(AssistantIntegrationDisabled):
        AssistantClient()
    post.assert_not_called()


@override_settings(STAGING_MODE=True)
def test_docs_analyzer_refuses_on_staging():
    from school.document_ai import AnalyzerDisabledError, call_docs_analyzer

    with patch("school.document_ai.requests.post") as post, pytest.raises(AnalyzerDisabledError):
        call_docs_analyzer(payload={"documents": []})
    post.assert_not_called()


@override_settings(STAGING_MODE=True)
def test_celery_startup_task_not_scheduled_on_staging():
    from config.celery import bootstrap_schedule

    with patch("classes.tasks.ensure_weekly_schedule_task.delay") as delay:
        bootstrap_schedule(sender=None)
    delay.assert_not_called()


@override_settings(STAGING_MODE=True)
def test_telegram_bot_does_not_start_on_staging():
    from tg_bot.bot import staging_guard

    assert staging_guard() is True


@pytest.mark.django_db
@override_settings(STAGING_MODE=True, BOT_TOKEN="")
def test_results_telegram_publication_skipped_without_token():
    from bot.models import TelegramChat
    from results.models import ResultsEvent
    from results.telegram import publish

    channel = TelegramChat.objects.create(chat_id=-1, type=TelegramChat.ChatType.CHANNEL, title="C")
    event = ResultsEvent.objects.create(
        title="E", telegram_enabled=True, telegram_channel=channel, source_kind="manual"
    )
    with patch("results.telegram._call") as call:
        assert publish(event.pk) is False
    call.assert_not_called()


def test_staging_compose_override_isolates_ports_and_disables_bot():
    text = (ROOT / "docker-compose.staging.yml").read_text()
    assert "ports: !override" in text and '"127.0.0.1:8020:8000"' in text and ":8010" not in text
    assert 'profiles: ["never-on-staging"]' in text
    services = ("migrate", "web", "bot", "celery_worker_docs", "celery_worker_default", "results_worker")
    blocks, current = {}, None
    for line in text.splitlines():
        if line.startswith("  ") and not line.startswith("   ") and line.strip().endswith(":"):
            current = line.strip()[:-1]
            blocks[current] = ""
        elif current:
            blocks[current] += line + "\n"
    for service in services:
        assert 'STAGING_MODE: "True"' in blocks[service], service


# --- Скрипты -----------------------------------------------------------------------------------


def _refresh(tmp_path, env_text, **extra_env):
    (tmp_path / ".env").write_text(env_text)
    (tmp_path / ".env.postgres").write_text("POSTGRES_DB=db\nPOSTGRES_USER=u\nPOSTGRES_PASSWORD=p\n")
    env = {"PATH": os.environ["PATH"], "SOURCE_DIR": str(tmp_path / "prod"), **extra_env}
    return subprocess.run(
        ["bash", str(ROOT / "scripts/staging_refresh.sh"), "--dry-run"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )


def test_refresh_refuses_without_staging_mode(tmp_path):
    result = _refresh(tmp_path, "WEB_BIND=127.0.0.1:8020\n")
    assert result.returncode != 0 and "STAGING_MODE" in result.stderr


def test_refresh_refuses_production_port(tmp_path):
    result = _refresh(tmp_path, "STAGING_MODE=True\nWEB_BIND=127.0.0.1:8010\n")
    assert result.returncode != 0 and "8010" in result.stderr


def test_refresh_refuses_production_project_name(tmp_path):
    result = _refresh(tmp_path, "STAGING_MODE=True\nWEB_BIND=127.0.0.1:8020\n", STAGING_PROJECT="sport_school")
    assert result.returncode != 0


def test_refresh_refuses_to_run_in_production_dir(tmp_path):
    result = _refresh(tmp_path, "STAGING_MODE=True\nWEB_BIND=127.0.0.1:8020\n", SOURCE_DIR=str(tmp_path))
    assert result.returncode != 0 and "каталог прода" in result.stderr


def test_refresh_dry_run_passes_in_valid_staging_dir(tmp_path):
    result = _refresh(tmp_path, "STAGING_MODE=True\nWEB_BIND=127.0.0.1:8020\n")
    assert result.returncode == 0, result.stderr
    assert "sport_school-postgres-1 → sport_school_staging-postgres-1" in result.stdout


def test_scripts_have_valid_syntax():
    for script in ("backup_db.sh", "staging_refresh.sh", "staging_compose.sh"):
        subprocess.run(["bash", "-n", str(ROOT / "scripts" / script)], check=True)


@pytest.mark.django_db
def test_anonymize_sql_clears_contacts():
    from django.db import connection

    from bot.models import TelegramChat, TelegramParticipant
    from users.models import User

    from .factories import PersonFactory

    person = PersonFactory(phone="+70000000000", email="p@example.org", telegram="@p", telegram_id=42)
    user = User.objects.create(email="real@example.org", phone="+7000", tg_id=99, tg_username="real", token="t")
    chat = TelegramChat.objects.create(chat_id=-5, type=TelegramChat.ChatType.GROUP, title="G")
    TelegramParticipant.objects.create(chat=chat, user_id=1, username="someone")
    sql = (ROOT / "scripts/sql/staging_anonymize.sql").read_text()
    with connection.cursor() as cursor:
        cursor.execute(sql.replace("BEGIN;", "").replace("COMMIT;", ""))
    person.refresh_from_db()
    user.refresh_from_db()
    assert (person.phone, person.email, person.telegram, person.telegram_id) == (None, None, None, None)
    assert user.email == f"user{user.pk}@staging.invalid" and user.tg_id is None and user.phone is None
    assert TelegramParticipant.objects.get().username == ""


def _compose_blocks(text: str) -> dict[str, str]:
    blocks, current = {}, None
    for line in text.splitlines():
        if line.startswith("  ") and not line.startswith("   ") and line.strip().endswith(":"):
            current = line.strip()[:-1]
            blocks[current] = ""
        elif current:
            blocks[current] += line + "\n"
    return blocks


APP_SERVICES = ("migrate", "web", "bot", "celery_worker_docs", "celery_worker_default", "results_worker")


def test_d01_staging_services_use_own_image_tag():
    blocks = _compose_blocks((ROOT / "docker-compose.staging.yml").read_text())
    for service in APP_SERVICES:
        assert "image: sport_school:staging" in blocks[service], service
    prod = (ROOT / "docker-compose.yml").read_text()
    assert "image: sport_school:latest" in prod and "sport_school:staging" not in prod  # прод не меняется


def test_d03_staging_worker_runs_without_beat_and_schedule_is_empty():
    blocks = _compose_blocks((ROOT / "docker-compose.staging.yml").read_text())
    command = next(line for line in blocks["celery_worker_default"].splitlines() if "command:" in line)
    assert "worker" in command and " -B" not in command and "--beat" not in command
    assert _settings_with({"STAGING_MODE": "True"})["beat"] == []


def test_d02_backup_is_private_regardless_of_umask(tmp_path):
    """Скрипт бэкапа с заглушкой docker и umask 022: каталог 0700, дамп 0600."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_docker = bin_dir / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env bash\n"
        'case "$1 $2" in\n'
        '  "inspect "*) exit 0 ;;\n'
        '  "exec -i") cat >/dev/null; exit 0 ;;\n'
        '  "exec "*) printf "PGDMP-synthetic" ;;\n'
        "esac\n"
    )
    fake_docker.chmod(0o755)
    work = tmp_path / "proj"
    work.mkdir()
    (work / ".env.postgres").write_text("POSTGRES_DB=db\nPOSTGRES_USER=u\nPOSTGRES_PASSWORD=p\n")
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "COMPOSE_PROJECT_NAME": "synthetic"}
    result = subprocess.run(
        ["bash", "-c", f"umask 022 && bash {ROOT / 'scripts/backup_db.sh'}"],
        cwd=work,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    backups = work / "backups"
    dumps = list(backups.glob("synthetic_*.dump"))
    assert len(dumps) == 1
    assert oct(backups.stat().st_mode & 0o777) == "0o700"
    assert oct(dumps[0].stat().st_mode & 0o777) == "0o600"
