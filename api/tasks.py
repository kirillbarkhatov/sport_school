import logging

from celery import shared_task

from api.services import (
    launch_online_results_stream,
    process_online_results_webhook_event,
    publish_online_results_event_to_telegram,
    publish_online_results_stream_to_telegram,
    stop_online_results_stream,
)
from api.telemetry import log_event

logger = logging.getLogger(__name__)


@shared_task
def process_online_results_webhook_event_task(event_id: int) -> None:
    try:
        log_event("task_started", task="process_webhook", event_id=event_id)
        process_online_results_webhook_event(event_id=event_id)
        log_event("task_finished", task="process_webhook", event_id=event_id)
    except Exception:
        logger.exception("Online Results webhook task failed: event_id=%s", event_id)
        log_event("task_failed", task="process_webhook", event_id=event_id)
        raise


@shared_task
def launch_online_results_stream_task(run_id: int) -> None:
    try:
        launch_online_results_stream(run_id=run_id)
    except Exception:
        logger.exception("Online Results stream task failed: run_id=%s", run_id)
        raise


@shared_task
def stop_online_results_stream_task(run_id: int, reason: str = "manual_stop") -> None:
    try:
        stop_online_results_stream(run_id=run_id, reason=reason)
    except Exception:
        logger.exception("Online Results stream stop task failed: run_id=%s", run_id)
        raise


@shared_task
def publish_online_results_event_to_telegram_task(event_id: int) -> None:
    try:
        publish_online_results_event_to_telegram(event_id=event_id)
    except Exception:
        logger.exception("Online Results telegram publish task failed: event_id=%s", event_id)
        raise


@shared_task
def publish_online_results_stream_to_telegram_task(stream_id: str) -> None:
    try:
        log_event("task_started", task="publish_stream_tg", stream_id=stream_id)
        publish_online_results_stream_to_telegram(stream_id=stream_id)
        log_event("task_finished", task="publish_stream_tg", stream_id=stream_id)
    except Exception:
        logger.exception("Online Results telegram stream publish task failed: stream_id=%s", stream_id)
        log_event("task_failed", task="publish_stream_tg", stream_id=stream_id)
        raise
