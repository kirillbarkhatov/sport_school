import logging

from celery import shared_task

from api.services import launch_online_results_stream, process_online_results_webhook_event, stop_online_results_stream

logger = logging.getLogger(__name__)


@shared_task
def process_online_results_webhook_event_task(event_id: int) -> None:
    try:
        process_online_results_webhook_event(event_id=event_id)
    except Exception:
        logger.exception("Online Results webhook task failed: event_id=%s", event_id)
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
