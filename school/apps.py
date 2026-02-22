import logging
import os
import sys

from django.apps import AppConfig
from django.core.cache import cache
from django.conf import settings

logger = logging.getLogger(__name__)


class SchoolConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "school"

    def ready(self):
        if not getattr(settings, "DOCS_SYNC_ON_STARTUP", True):
            return

        command = sys.argv[1] if len(sys.argv) > 1 else ""
        skip_commands = {
            "makemigrations",
            "migrate",
            "collectstatic",
            "shell",
            "dbshell",
            "test",
        }
        if command in skip_commands:
            return

        args_str = " ".join(sys.argv).lower()
        web_process_markers = ("runserver", "gunicorn", "uvicorn", "daphne")
        if not any(marker in args_str for marker in web_process_markers):
            return

        # Avoid duplicate run on dev autoreloader parent process.
        if command == "runserver" and os.environ.get("RUN_MAIN") != "true":
            return

        # Prevent duplicate startup scheduling across multi-process web servers
        # (e.g. several gunicorn workers booting at once).
        cooldown_sec = int(getattr(settings, "DOCS_SYNC_STARTUP_COOLDOWN_SEC", 180))
        lock_key = "school:docs_sync_startup_scheduled"
        if not cache.add(lock_key, "1", timeout=max(30, cooldown_sec)):
            logger.info("Пропускаем sync_documents_from_storage_task: уже запланирован недавно.")
            return

        try:
            from .tasks import sync_documents_from_storage_task

            # Startup sync should not auto-enqueue analysis to avoid background resource spikes.
            sync_documents_from_storage_task.delay(enqueue_analysis=False)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Не удалось поставить sync_documents_from_storage_task при старте: %s", exc)
