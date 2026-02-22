import logging
import os
import sys

from django.apps import AppConfig
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

        try:
            from .tasks import sync_documents_from_storage_task

            sync_documents_from_storage_task.delay()
        except Exception as exc:  # noqa: BLE001
            logger.debug("Не удалось поставить sync_documents_from_storage_task при старте: %s", exc)
