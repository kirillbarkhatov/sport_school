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

        if getattr(settings, "DOCS_SYNC_ON_STARTUP", True):
            cooldown_sec = int(getattr(settings, "DOCS_SYNC_STARTUP_COOLDOWN_SEC", 180))
            lock_key = "school:docs_sync_startup_scheduled"
            if cache.add(lock_key, "1", timeout=max(30, cooldown_sec)):
                try:
                    from .tasks import sync_documents_from_storage_task

                    # Startup sync should not auto-enqueue analysis to avoid background resource spikes.
                    sync_documents_from_storage_task.delay(enqueue_analysis=False)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Не удалось поставить sync_documents_from_storage_task при старте: %s", exc)
            else:
                logger.info("Пропускаем sync_documents_from_storage_task: уже запланирован недавно.")

        validity_lock_key = "school:certificate_reconcile_startup_scheduled"
        if cache.add(validity_lock_key, "1", timeout=180):
            try:
                from .tasks import reconcile_athlete_certificate_validity

                reconcile_athlete_certificate_validity.delay()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Не удалось поставить reconcile_athlete_certificate_validity при старте: %s", exc)

        rebind_lock_key = "school:competition_docs_rebind_startup_scheduled"
        if cache.add(rebind_lock_key, "1", timeout=180):
            try:
                from .tasks import rebind_unbound_competition_documents_task

                rebind_unbound_competition_documents_task.delay()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Не удалось поставить rebind_unbound_competition_documents_task при старте: %s", exc)
