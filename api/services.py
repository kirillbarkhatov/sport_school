import logging
from json import JSONDecodeError
import json
from urllib import error, request

from django.conf import settings
from django.utils import timezone

from api.models import StreamRun
from api.models import WebhookEvent

logger = logging.getLogger(__name__)


def process_online_results_webhook_event(event_id: int) -> None:
    """
    Heavy business logic entrypoint for Online Results webhook.
    Keep webhook HTTP handler fast by doing processing outside the request cycle.
    """
    event = WebhookEvent.objects.filter(id=event_id).first()
    if event is None:
        logger.warning("WebhookEvent not found for processing: id=%s", event_id)
        return

    logger.info(
        "Processed Online Results webhook event: id=%s stream_id=%s event_type=%s payload_hash=%s",
        event.id,
        event.stream_id,
        event.event_type,
        event.payload_hash,
    )


def launch_online_results_stream(run_id: int) -> None:
    run = StreamRun.objects.filter(id=run_id).first()
    if run is None:
        logger.warning("StreamRun not found for launch: id=%s", run_id)
        return

    run.status = StreamRun.Status.RUNNING
    run.started_at = timezone.now()
    run.last_error = ""
    run.save(update_fields=["status", "started_at", "last_error", "updated_at"])

    start_url = settings.ONLINE_RESULTS_STREAM_START_URL
    timeout_sec = settings.ONLINE_RESULTS_STREAM_TIMEOUT_SEC
    auth_token = settings.ONLINE_RESULTS_STREAM_AUTH_TOKEN

    if not start_url:
        run.status = StreamRun.Status.FAILED
        run.finished_at = timezone.now()
        run.last_error = "ONLINE_RESULTS_STREAM_START_URL is not configured."
        run.save(update_fields=["status", "finished_at", "last_error", "updated_at"])
        logger.warning("StreamRun start URL is not configured: id=%s stream_id=%s", run.id, run.stream_id)
        return

    outbound_payload: dict[str, object] = {
        "protocol_link": run.protocol_link,
        "poll_interval_sec": settings.ONLINE_RESULTS_STREAM_POLL_INTERVAL_SEC,
        "refresh_titles_every": settings.ONLINE_RESULTS_STREAM_REFRESH_TITLES_EVERY,
        "finalize_timeout_sec": settings.ONLINE_RESULTS_STREAM_FINALIZE_TIMEOUT_SEC,
        "finalize_max_missing": settings.ONLINE_RESULTS_STREAM_FINALIZE_MAX_MISSING,
        "callback_url": run.callback_url,
        "callback_secret": settings.ONLINE_RESULTS_WEBHOOK_SECRET,
        "worker_print": settings.ONLINE_RESULTS_STREAM_WORKER_PRINT,
    }
    if run.launch_payload_json:
        outbound_payload.update(run.launch_payload_json)

    headers = {"Content-Type": "application/json"}
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    req = request.Request(
        url=start_url,
        method="POST",
        data=json.dumps(outbound_payload).encode("utf-8"),
        headers=headers,
    )

    try:
        with request.urlopen(req, timeout=timeout_sec) as response:  # noqa: S310
            raw_response = response.read().decode("utf-8")
            response_json = _safe_json(raw_response)
            run.external_response_json = response_json
            remote_stream_id = str(response_json.get("stream_id") or "")
            if remote_stream_id:
                run.stream_id = remote_stream_id
            run.external_run_id = str(response_json.get("run_id") or response_json.get("id") or "")
            run.status = StreamRun.Status.SUCCESS
            run.finished_at = timezone.now()
            run.save(
                update_fields=[
                    "stream_id",
                    "external_response_json",
                    "external_run_id",
                    "status",
                    "finished_at",
                    "updated_at",
                ]
            )
            logger.info(
                "StreamRun started successfully: id=%s stream_id=%s external_run_id=%s",
                run.id,
                run.stream_id,
                run.external_run_id,
            )
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        run.status = StreamRun.Status.FAILED
        run.finished_at = timezone.now()
        run.last_error = f"http_{exc.code}"
        run.external_response_json = _safe_json(body)
        run.save(
            update_fields=[
                "status",
                "finished_at",
                "last_error",
                "external_response_json",
                "updated_at",
            ]
        )
        logger.warning(
            "StreamRun start failed: id=%s stream_id=%s status_code=%s",
            run.id,
            run.stream_id,
            exc.code,
        )
    except (error.URLError, TimeoutError, OSError) as exc:
        run.status = StreamRun.Status.FAILED
        run.finished_at = timezone.now()
        run.last_error = str(exc)[:500]
        run.save(update_fields=["status", "finished_at", "last_error", "updated_at"])
        logger.warning(
            "StreamRun transport error: id=%s stream_id=%s error=%s",
            run.id,
            run.stream_id,
            run.last_error,
        )


def _safe_json(raw_response: str) -> dict[str, object]:
    if not raw_response:
        return {}
    try:
        data = json.loads(raw_response)
    except JSONDecodeError:
        return {"raw_response": raw_response[:2000]}
    if isinstance(data, dict):
        return data
    return {"data": data}
