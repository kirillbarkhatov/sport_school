import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


def _keys(event_id: int) -> tuple[str, str]:
    return f"results:tg:lock:{event_id}", f"results:tg:pending:{event_id}"


@shared_task(ignore_result=True)
def publish_event_to_telegram(event_id: int) -> None:
    """Одна публикация на трансляцию за раз. Изменения во время публикации не теряются: ставится флаг pending,
    и по окончании задача перезапускается с паузой (лимиты Telegram на редактирование)."""
    from .telegram import publish

    lock_key, pending_key = _keys(event_id)
    if not cache.add(lock_key, "1", timeout=120):
        cache.set(pending_key, "1", timeout=300)
        return
    try:
        publish(event_id)
    except Exception:
        logger.exception("results: публикация в Telegram не удалась event=%s", event_id)
    finally:
        cache.delete(lock_key)
    if cache.delete(pending_key):
        interval = float(getattr(settings, "RESULTS_TELEGRAM_MIN_INTERVAL_SEC", 2.0))
        publish_event_to_telegram.apply_async((event_id,), countdown=interval)


def enqueue_publication(event_id: int) -> None:
    if getattr(settings, "RESULTS_TELEGRAM_SYNC", False):
        publish_event_to_telegram(event_id)
        return
    try:
        publish_event_to_telegram.delay(event_id)
    except Exception:  # брокер недоступен — трансляция не должна падать
        logger.exception("results: не удалось поставить публикацию в очередь event=%s", event_id)
