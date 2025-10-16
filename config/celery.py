import logging
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

app = Celery("config")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


logger = logging.getLogger(__name__)


@app.on_after_finalize.connect
def bootstrap_schedule(sender, **kwargs):
    try:
        from classes.tasks import ensure_weekly_schedule_task

        ensure_weekly_schedule_task.delay()
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            "Не удалось запланировать ensure_weekly_schedule_task при запуске: %s",
            exc,
        )
