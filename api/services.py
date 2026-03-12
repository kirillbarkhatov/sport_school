import logging
from json import JSONDecodeError
import json
from datetime import datetime
import sys
import hashlib
import uuid
import zlib
from contextlib import contextmanager
from time import perf_counter
from urllib import error, request
from urllib.parse import quote

from django.conf import settings
from django.core.cache import cache
from django.db import connection, transaction
from django.db.models import QuerySet
from django.utils import timezone

from api.models import StreamRun, extract_source_id
from api.models import WebhookEvent
from api.telegram_streaming import SUPPORTED_EVENT_TYPES
from api.telemetry import log_event

logger = logging.getLogger(__name__)


def normalize_source_id(protocol_link: str) -> str:
    return extract_source_id((protocol_link or "").strip())


def process_online_results_webhook_event(event_id: int) -> None:
    """
    Heavy business logic entrypoint for Online Results webhook.
    Keep webhook HTTP handler fast by doing processing outside the request cycle.
    """
    event = WebhookEvent.objects.filter(id=event_id).first()
    if event is None:
        logger.warning("WebhookEvent not found for processing: id=%s", event_id)
        log_event("webhook_process_missing_event", event_id=event_id)
        return

    started = perf_counter()
    log_event(
        "webhook_process_started",
        event_id=event.id,
        stream_id=event.stream_id,
        event_type=event.event_type,
    )
    enqueue_telegram = False
    with _stream_webhook_processing_lock(event.stream_id):
        with transaction.atomic():
            run = _find_stream_run_by_stream_id(event.stream_id)
            if run:
                _apply_webhook_event_to_stream_run(run=run, event=event)
                enqueue_telegram = True
            else:
                logger.warning(
                    "StreamRun not found for webhook event: event_id=%s stream_id=%s event_type=%s",
                    event.id,
                    event.stream_id,
                    event.event_type,
                )
                log_event(
                    "webhook_process_run_missing",
                    event_id=event.id,
                    stream_id=event.stream_id,
                    event_type=event.event_type,
                )
    if enqueue_telegram:
        _enqueue_telegram_publication(event_id=event.id)

    logger.info(
        "Processed Online Results webhook event: id=%s stream_id=%s event_type=%s payload_hash=%s",
        event.id,
        event.stream_id,
        event.event_type,
        event.payload_hash,
    )
    run_state = _find_stream_run_by_stream_id(event.stream_id)
    log_event(
        "webhook_process_finished",
        event_id=event.id,
        stream_id=event.stream_id,
        event_type=event.event_type,
        duration_ms=int((perf_counter() - started) * 1000),
        run_status=(run_state.status if run_state else "missing"),
        tg_enabled=bool(run_state and run_state.telegram_publish_enabled),
    )


def launch_online_results_stream(run_id: int) -> None:
    run = StreamRun.objects.filter(id=run_id).first()
    if run is None:
        logger.warning("StreamRun not found for launch: id=%s", run_id)
        return

    if not run.source_id and run.protocol_link:
        run.source_id = normalize_source_id(run.protocol_link)
    run.status = StreamRun.Status.RUNNING
    run.started_at = timezone.now()
    run.last_error = ""
    run.last_requested_at = timezone.now()
    run.save(update_fields=["source_id", "status", "started_at", "last_error", "last_requested_at", "updated_at"])

    _stop_duplicate_running_streams(current_run=run)

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
            current_state = _normalize_state(run.external_response_json)
            current_state["start_response"] = response_json
            remote_stream_id = str(response_json.get("stream_id") or "")
            if remote_stream_id:
                run.stream_id = remote_stream_id
            run.external_run_id = str(response_json.get("run_id") or response_json.get("id") or "")
            run.external_response_json = current_state
            run.status = StreamRun.Status.RUNNING
            run.finished_at = None
            run.save(
                update_fields=[
                    "stream_id",
                    "external_response_json",
                    "external_run_id",
                    "status",
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


def stop_online_results_stream(run_id: int, reason: str = "manual_stop") -> None:
    run = StreamRun.objects.filter(id=run_id).first()
    if run is None:
        logger.warning("StreamRun not found for stop: id=%s", run_id)
        return

    stop_url = _build_stop_url(run.stream_id)
    stop_error = ""
    stop_response_json: dict[str, object] = {}

    headers = {"Content-Type": "application/json"}
    auth_token = settings.ONLINE_RESULTS_STREAM_AUTH_TOKEN
    timeout_sec = settings.ONLINE_RESULTS_STREAM_TIMEOUT_SEC
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    if stop_url and run.stream_id and not run.stream_id.startswith("pending-"):
        req = request.Request(url=stop_url, method="POST", data=b"", headers=headers)
        try:
            with request.urlopen(req, timeout=timeout_sec) as response:  # noqa: S310
                raw_response = response.read().decode("utf-8")
                stop_response_json = _safe_json(raw_response)
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            stop_response_json = _safe_json(body)
            stop_error = f"http_{exc.code}"
        except (error.URLError, TimeoutError, OSError) as exc:
            stop_error = str(exc)[:500]

    state = _normalize_state(run.external_response_json)
    state["stop_response"] = stop_response_json
    if stop_error:
        state["stop_error"] = stop_error

    run.external_response_json = state
    run.status = StreamRun.Status.STOPPED
    run.finished_at = timezone.now()
    run.last_error = (f"{reason}; stop_error={stop_error}" if stop_error else reason)[:500]
    run.save(
        update_fields=[
            "external_response_json",
            "status",
            "finished_at",
            "last_error",
            "updated_at",
        ]
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


def reset_online_results_stream_state(run_id: int, *, requested_by=None) -> StreamRun:
    source_run = StreamRun.objects.filter(id=run_id).first()
    if source_run is None:
        raise ValueError("StreamRun not found")

    if source_run.status in {StreamRun.Status.RUNNING, StreamRun.Status.PENDING}:
        stop_online_results_stream(run_id=source_run.id, reason="manual_hard_refresh")

    _reset_remote_stream_state(source_run.stream_id)

    WebhookEvent.objects.filter(stream_id=source_run.stream_id).delete()
    cache.clear()
    StreamRun.objects.filter(pk=source_run.pk).update(
        external_response_json={},
        telegram_resume_from_event_id=None,
        updated_at=timezone.now(),
    )

    launch_payload = dict(source_run.launch_payload_json or {})
    created_by = requested_by if requested_by is not None and getattr(requested_by, "is_authenticated", False) else source_run.created_by

    replacement = StreamRun.objects.create(
        stream_id=f"pending-{uuid.uuid4().hex[:10]}",
        protocol_link=source_run.protocol_link,
        source_id=source_run.source_id or normalize_source_id(source_run.protocol_link),
        stream_type=source_run.stream_type,
        status=StreamRun.Status.PENDING,
        launch_payload_json=launch_payload,
        callback_url=source_run.callback_url,
        created_by=created_by,
        competition=source_run.competition,
    )
    launch_online_results_stream(replacement.id)
    replacement.refresh_from_db()

    if source_run.telegram_publish_enabled and source_run.telegram_channel_id:
        from api.telegram_streaming import enable_stream_telegram_publication

        enable_stream_telegram_publication(run=replacement, channel_id=int(source_run.telegram_channel_id))

    return replacement


def _build_stop_url(stream_id: str) -> str:
    template = getattr(settings, "ONLINE_RESULTS_STREAM_STOP_URL_TEMPLATE", "").strip()
    if template and "{stream_id}" in template:
        return template.replace("{stream_id}", quote(stream_id, safe=""))

    start_url = settings.ONLINE_RESULTS_STREAM_START_URL.strip()
    suffix = "/v1/streams"
    if start_url.endswith(suffix):
        return f"{start_url}/{quote(stream_id, safe='')}/stop"
    return ""


def _build_reset_state_url(stream_id: str) -> str:
    template = str(getattr(settings, "ONLINE_RESULTS_STREAM_RESET_STATE_URL_TEMPLATE", "") or "").strip()
    if template and "{stream_id}" in template:
        return template.replace("{stream_id}", quote(stream_id, safe=""))

    start_url = str(getattr(settings, "ONLINE_RESULTS_STREAM_START_URL", "") or "").strip()
    suffix = "/v1/streams"
    if start_url.endswith(suffix):
        return f"{start_url}/{quote(stream_id, safe='')}/reset-state"
    return ""


def _reset_remote_stream_state(stream_id: str) -> None:
    stream_id = (stream_id or "").strip()
    if not stream_id or stream_id.startswith("pending-"):
        return
    reset_url = _build_reset_state_url(stream_id)
    if not reset_url:
        return
    headers = {"Content-Type": "application/json"}
    auth_token = str(getattr(settings, "ONLINE_RESULTS_STREAM_AUTH_TOKEN", "") or "").strip()
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    timeout_sec = int(getattr(settings, "ONLINE_RESULTS_STREAM_TIMEOUT_SEC", 15))
    req = request.Request(url=reset_url, method="POST", data=b"", headers=headers)
    try:
        with request.urlopen(req, timeout=timeout_sec):  # noqa: S310
            return
    except Exception as exc:
        raise RuntimeError(f"remote_reset_failed: {exc}") from exc


def _stop_duplicate_running_streams(current_run: StreamRun) -> None:
    if current_run.source_id:
        duplicates = (
            StreamRun.objects.filter(source_id=current_run.source_id, status=StreamRun.Status.RUNNING)
            .exclude(id=current_run.id)
            .order_by("-created_at")
        )
    else:
        duplicates = (
            StreamRun.objects.filter(protocol_link=current_run.protocol_link, status=StreamRun.Status.RUNNING)
            .exclude(id=current_run.id)
            .order_by("-created_at")
        )
    for duplicate in duplicates:
        logger.info(
            "Stopping duplicate stream before new launch: old_run_id=%s old_stream_id=%s new_run_id=%s",
            duplicate.id,
            duplicate.stream_id,
            current_run.id,
        )
        stop_online_results_stream(run_id=duplicate.id, reason=f"replaced_by_run_{current_run.id}")


def _find_stream_run_by_stream_id(stream_id: str) -> StreamRun | None:
    if not stream_id:
        return None
    queryset: QuerySet[StreamRun] = StreamRun.objects.filter(stream_id=stream_id).order_by("-created_at")
    return queryset.first()


def _apply_webhook_event_to_stream_run(run: StreamRun, event: WebhookEvent) -> None:
    run = StreamRun.objects.select_for_update().filter(pk=run.pk).first()
    if run is None:
        return
    payload_wrapper = event.payload_json if isinstance(event.payload_json, dict) else {}
    payload = payload_wrapper.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    event_type = event.event_type or str(payload_wrapper.get("event_type") or "")
    schema_version = int(payload.get("schema_version") or payload_wrapper.get("schema_version") or 1)
    event_time = event.event_time or event.received_at or timezone.now()
    state = _normalize_state(run.external_response_json)
    stream_output = state.setdefault("stream_output", {})
    if not isinstance(stream_output, dict):
        stream_output = {}
        state["stream_output"] = stream_output

    counters = stream_output.setdefault("event_counters", {})
    if isinstance(counters, dict):
        counters[event_type] = int(counters.get(event_type, 0)) + 1

    stream_output["last_event"] = {
        "event_id": event.id,
        "event_type": event_type,
        "schema_version": schema_version,
        "event_time": event_time.isoformat(),
        "received_at": event.received_at.isoformat() if event.received_at else "",
    }

    update_fields = {"external_response_json", "updated_at"}
    if event_type in {
        "stream_started",
        "stream_snapshot",
        "stream_warning",
        "start_forecast_updated",
        "tick",
        "result_updated",
        "group_table_updated",
        "group_completed",
        "overall_completed",
        "kanaev_summary_updated",
    }:
        if run.status == StreamRun.Status.PENDING:
            run.status = StreamRun.Status.RUNNING
            update_fields.add("status")
        if run.started_at is None and event_type == "stream_started":
            run.started_at = event_time
            update_fields.add("started_at")
        if event_type == "stream_snapshot":
            stream_output["competition_phase"] = str(payload.get("competition_phase") or "running")
            stream_output["competition_format"] = str(payload.get("competition_format") or stream_output.get("competition_format") or "two_run")
            stream_output["status_text"] = str(payload.get("status_text") or "")
            snapshot_current_group_key = str(payload.get("current_group_key") or "").strip()
            if snapshot_current_group_key:
                stream_output["current_group_key"] = snapshot_current_group_key
            stream_output["competition_title"] = str(payload.get("competition_title") or stream_output.get("competition_title") or "")
            teams = payload.get("teams")
            if isinstance(teams, list):
                normalized_teams = sorted({str(team).strip() for team in teams if str(team).strip()}, key=str.lower)
                stream_output["teams"] = normalized_teams
            athletes = payload.get("athletes")
            if isinstance(athletes, list):
                stream_output["snapshot_athletes"] = athletes
            groups = payload.get("groups")
            if isinstance(groups, list):
                tables = stream_output.setdefault("latest_group_tables", {})
                if isinstance(tables, dict):
                    for item in groups:
                        if not isinstance(item, dict):
                            continue
                        group_key = str(item.get("group_key") or "")
                        if not group_key:
                            continue
                        data = item.get("data")
                        lines_plain = []
                        if isinstance(data, dict):
                            lines_plain = _payload_lines(data.get("lines_plain"))
                        tables[group_key] = {
                            "sheet_name": str(item.get("sheet_name") or ""),
                            "group_name": str(item.get("group_name") or ""),
                            "order_index": int(item.get("order_index") or 0),
                            "lines": lines_plain,
                            "lines_plain": lines_plain,
                            "data": data if isinstance(data, dict) else {},
                            "is_finalized": bool(item.get("is_finalized")),
                            "last_updated_at": event_time.isoformat(),
                        }
                        run_stage = int(item.get("run_stage") or 0)
                        if run_stage in {1, 2}:
                            competition_format = str(stream_output.get("competition_format") or "two_run")
                            completed_groups = stream_output.setdefault("completed_groups", {})
                            if isinstance(completed_groups, dict):
                                run_label = _build_run_label(run_stage=run_stage, competition_format=competition_format)
                                completed_data = data if isinstance(data, dict) else {}
                                completed_lines = lines_plain
                                if run_stage == 1:
                                    completed_data = _build_run1_group_table_snapshot(completed_data)
                                    completed_lines = _payload_lines(completed_data.get("lines_plain")) or completed_lines
                                completed_groups[_build_completed_group_key(group_key=group_key, run_stage=run_stage)] = {
                                    "group_key": group_key,
                                    "sheet_name": str(item.get("sheet_name") or ""),
                                    "group_name": str(item.get("group_name") or ""),
                                    "order_index": int(item.get("order_index") or 0),
                                    "table_lines": completed_lines,
                                    "table_lines_plain": completed_lines,
                                    "club_stats_lines": [],
                                    "club_stats_lines_plain": [],
                                    "data": {"group_table": completed_data} if isinstance(completed_data, dict) else {},
                                    "run_stage": run_stage,
                                    "run_label": run_label,
                                    "option_label": _build_option_label(
                                        group_name=str(item.get("group_name") or ""),
                                        run_label=run_label,
                                        competition_format=competition_format,
                                    ),
                                    "finalized_at": event_time.isoformat(),
                                    "last_updated_at": event_time.isoformat(),
                                    "is_finalized": True,
                                }
                                if run_stage == 2:
                                    run1_data = _build_run1_group_table_snapshot(data if isinstance(data, dict) else {})
                                    _upsert_completed_snapshot(
                                        completed_groups=completed_groups,
                                        group_key=group_key,
                                        sheet_name=str(item.get("sheet_name") or ""),
                                        group_name=str(item.get("group_name") or ""),
                                        order_index=int(item.get("order_index") or 0),
                                        run_stage=1,
                                        data=run1_data,
                                        lines_plain=_payload_lines(run1_data.get("lines_plain")),
                                        event_time=event_time,
                                        competition_format=competition_format,
                                    )
            _reconcile_focus_state(stream_output=stream_output, event_time=event_time)
            _initialize_focus_from_current_group(stream_output=stream_output, event_time=event_time)
        elif event_type == "stream_warning":
            stream_output["last_warning"] = {
                "warning_type": str(payload.get("warning_type") or ""),
                "warning": str(payload.get("warning") or ""),
                "error_type": str(payload.get("error_type") or ""),
                "attempt": int(payload.get("attempt") or 0),
                "next_retry_sec": float(payload.get("next_retry_sec") or 0),
                "at": str(payload.get("at") or event_time.isoformat()),
            }
        elif event_type == "start_forecast_updated":
            rows = payload.get("rows")
            if isinstance(rows, list):
                stream_output["start_forecast"] = {
                    "rows": rows,
                    "updated_at": event_time.isoformat(),
                }
            stream_output["competition_phase"] = str(payload.get("competition_phase") or stream_output.get("competition_phase") or "running")
            stream_output["competition_format"] = str(payload.get("competition_format") or stream_output.get("competition_format") or "two_run")
            stream_output["status_text"] = str(payload.get("status_text") or stream_output.get("status_text") or "")
            forecast_current_group_key = str(payload.get("current_group_key") or "").strip()
            if forecast_current_group_key and not str(stream_output.get("current_group_key") or "").strip():
                stream_output["current_group_key"] = forecast_current_group_key
        elif event_type == "tick":
            stream_output["last_tick"] = {
                "ts": str(payload.get("ts") or ""),
                "changed_count": int(payload.get("changed_count") or 0),
            }
            stream_output["competition_phase"] = str(payload.get("competition_phase") or stream_output.get("competition_phase") or "running")
            stream_output["competition_format"] = str(payload.get("competition_format") or stream_output.get("competition_format") or "two_run")
            stream_output["status_text"] = str(payload.get("status_text") or stream_output.get("status_text") or "")
            tick_current_group_key = str(payload.get("current_group_key") or "").strip()
            if tick_current_group_key and not str(stream_output.get("current_group_key") or "").strip():
                stream_output["current_group_key"] = tick_current_group_key
        elif event_type == "result_updated":
            lines = _payload_lines(payload.get("lines"))
            if lines:
                stream_output["last_result_lines"] = lines
                _log_console_lines(prefix="result_updated", lines=lines)
            data = payload.get("data")
            if isinstance(data, dict):
                stream_output["last_result_data"] = data
                stream_output["last_result_event_id"] = event.id
                _update_focus_from_result_data(stream_output=stream_output, data=data, event_time=event_time)
        elif event_type == "group_table_updated":
            group_key = str(payload.get("group_key") or "")
            lines = _payload_lines(payload.get("lines"))
            data = payload.get("data")
            if group_key and (lines or isinstance(data, dict)):
                tables = stream_output.setdefault("latest_group_tables", {})
                if isinstance(tables, dict):
                    previous_table = tables.get(group_key, {}) if isinstance(tables.get(group_key), dict) else {}
                    tables[group_key] = {
                        "sheet_name": str(payload.get("sheet_name") or ""),
                        "group_name": str(payload.get("group_name") or ""),
                        "order_index": int(payload.get("order_index") or previous_table.get("order_index") or 0),
                        "lines": lines,
                        "lines_plain": _payload_lines(payload.get("lines_plain")) or lines,
                        "data": data if isinstance(data, dict) else {},
                        "is_finalized": bool(
                            previous_table.get("is_finalized")
                            if isinstance(previous_table, dict)
                            else False
                        ),
                        "last_updated_at": event_time.isoformat(),
                    }
                    # If finalized group is updated later, sync the matching "group+run" snapshot too.
                    run_stage = _detect_run_stage_from_payload(payload)
                    competition_format = str(stream_output.get("competition_format") or "two_run")
                    completed_key = _build_completed_group_key(group_key=group_key, run_stage=run_stage)
                    completed_groups = stream_output.setdefault("completed_groups", {})
                    if isinstance(completed_groups, dict) and isinstance(completed_groups.get(completed_key), dict):
                        completed_item = completed_groups.get(completed_key, {})
                        run_label = _build_run_label(run_stage=run_stage, competition_format=competition_format)
                        completed_data = data if isinstance(data, dict) else (completed_item.get("data") or {})
                        completed_lines = _payload_lines(payload.get("lines_plain")) or lines
                        if run_stage == 1:
                            completed_data = _build_run1_group_table_snapshot(completed_data if isinstance(completed_data, dict) else {})
                            completed_lines = _payload_lines(completed_data.get("lines_plain")) or completed_lines
                        completed_item.update(
                            {
                                "sheet_name": str(payload.get("sheet_name") or completed_item.get("sheet_name") or ""),
                                "group_name": str(payload.get("group_name") or completed_item.get("group_name") or ""),
                                "order_index": int(payload.get("order_index") or completed_item.get("order_index") or 0),
                                "table_lines": completed_lines or completed_item.get("table_lines") or [],
                                "table_lines_plain": completed_lines,
                                "data": completed_data,
                                "run_stage": run_stage,
                                "run_label": run_label,
                                "option_label": _build_option_label(
                                    group_name=str(payload.get("group_name") or completed_item.get("group_name") or ""),
                                    run_label=run_label,
                                    competition_format=competition_format,
                                ),
                                "last_updated_at": event_time.isoformat(),
                            }
                        )
                        completed_groups[completed_key] = completed_item
                _log_console_lines(prefix=f"group_table_updated:{group_key}", lines=lines)
                inferred_final_stage = _detect_finalized_run_stage_from_payload(payload)
                if inferred_final_stage in {1, 2}:
                    completed_key = _build_completed_group_key(group_key=group_key, run_stage=inferred_final_stage)
                    completed_groups = stream_output.setdefault("completed_groups", {})
                    if isinstance(completed_groups, dict):
                        existing = completed_groups.get(completed_key, {})
                        if not isinstance(existing, dict):
                            existing = {}
                        competition_format = str(stream_output.get("competition_format") or "two_run")
                        run_label = _build_run_label(
                            run_stage=inferred_final_stage,
                            competition_format=competition_format,
                        )
                        completed_data = data if isinstance(data, dict) else (existing.get("data") or {})
                        completed_lines = _payload_lines(payload.get("lines_plain")) or lines
                        if inferred_final_stage == 1:
                            completed_data = _build_run1_group_table_snapshot(completed_data if isinstance(completed_data, dict) else {})
                            completed_lines = _payload_lines(completed_data.get("lines_plain")) or completed_lines
                        completed_groups[completed_key] = {
                            "group_key": group_key,
                            "sheet_name": str(payload.get("sheet_name") or existing.get("sheet_name") or ""),
                            "group_name": str(payload.get("group_name") or existing.get("group_name") or ""),
                            "order_index": int(payload.get("order_index") or existing.get("order_index") or 0),
                            "table_lines": lines or existing.get("table_lines") or [],
                            "table_lines_plain": completed_lines,
                            "club_stats_lines": existing.get("club_stats_lines") or [],
                            "club_stats_lines_plain": existing.get("club_stats_lines_plain") or [],
                            "data": completed_data,
                            "run_stage": inferred_final_stage,
                            "run_label": run_label,
                            "option_label": _build_option_label(
                                group_name=str(payload.get("group_name") or existing.get("group_name") or ""),
                                run_label=run_label,
                                competition_format=competition_format,
                            ),
                            "finalized_at": str(existing.get("finalized_at") or event_time.isoformat()),
                            "last_updated_at": event_time.isoformat(),
                            "is_finalized": True,
                        }
                        if inferred_final_stage == 2:
                            run1_data = _build_run1_group_table_snapshot(completed_data if isinstance(completed_data, dict) else {})
                            _upsert_completed_snapshot(
                                completed_groups=completed_groups,
                                group_key=group_key,
                                sheet_name=str(payload.get("sheet_name") or existing.get("sheet_name") or ""),
                                group_name=str(payload.get("group_name") or existing.get("group_name") or ""),
                                order_index=int(payload.get("order_index") or existing.get("order_index") or 0),
                                run_stage=1,
                                data=run1_data,
                                lines_plain=_payload_lines(run1_data.get("lines_plain")),
                                event_time=event_time,
                                competition_format=competition_format,
                            )
                _reconcile_focus_state(stream_output=stream_output, event_time=event_time)
        elif event_type == "group_completed":
            group_key = str(payload.get("group_key") or "")
            table_lines = _payload_lines(payload.get("table_lines"))
            club_stats_lines = _payload_lines(payload.get("club_stats_lines"))
            if group_key:
                run_stage = _detect_run_stage_from_payload(payload)
                competition_format = str(stream_output.get("competition_format") or "two_run")
                run_label = _build_run_label(run_stage=run_stage, competition_format=competition_format)
                completed_key = _build_completed_group_key(group_key=group_key, run_stage=run_stage)
                completed_groups = stream_output.setdefault("completed_groups", {})
                if isinstance(completed_groups, dict):
                    completed_data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                    if run_stage == 1:
                        completed_data = _build_run1_group_table_snapshot(completed_data.get("group_table", completed_data))
                    completed_groups[completed_key] = {
                        "group_key": group_key,
                        "sheet_name": str(payload.get("sheet_name") or ""),
                        "group_name": str(payload.get("group_name") or ""),
                        "order_index": int(payload.get("order_index") or 0),
                        "table_lines": table_lines,
                        "table_lines_plain": _payload_lines(payload.get("table_lines_plain")) or table_lines,
                        "club_stats_lines": club_stats_lines,
                        "club_stats_lines_plain": _payload_lines(payload.get("club_stats_lines_plain")) or club_stats_lines,
                        "data": completed_data if isinstance(completed_data, dict) else {},
                        "run_stage": run_stage,
                        "run_label": run_label,
                        "option_label": _build_option_label(
                            group_name=str(payload.get("group_name") or ""),
                            run_label=run_label,
                            competition_format=competition_format,
                        ),
                        "finalized_at": event_time.isoformat(),
                        "last_updated_at": event_time.isoformat(),
                        "is_finalized": True,
                    }
                    if run_stage == 2:
                        run1_data = _build_run1_group_table_snapshot(
                            payload.get("data", {}).get("group_table")
                            if isinstance(payload.get("data"), dict)
                            else {}
                        )
                        _upsert_completed_snapshot(
                            completed_groups=completed_groups,
                            group_key=group_key,
                            sheet_name=str(payload.get("sheet_name") or ""),
                            group_name=str(payload.get("group_name") or ""),
                            order_index=int(payload.get("order_index") or 0),
                            run_stage=1,
                            data=run1_data,
                            lines_plain=_payload_lines(run1_data.get("lines_plain")),
                            event_time=event_time,
                            competition_format=competition_format,
                        )
                tables = stream_output.setdefault("latest_group_tables", {})
                if isinstance(tables, dict):
                    current = tables.get(group_key, {}) if isinstance(tables.get(group_key), dict) else {}
                    current.update(
                        {
                            "sheet_name": str(payload.get("sheet_name") or current.get("sheet_name") or ""),
                            "group_name": str(payload.get("group_name") or current.get("group_name") or ""),
                            "order_index": int(payload.get("order_index") or current.get("order_index") or 0),
                            "lines": table_lines or current.get("lines") or [],
                            "lines_plain": _payload_lines(payload.get("table_lines_plain"))
                            or current.get("lines_plain")
                            or table_lines,
                            "data": (
                                payload.get("data", {}).get("group_table")
                                if isinstance(payload.get("data"), dict)
                                else current.get("data", {})
                            ),
                            "is_finalized": True,
                            "finalized_at": event_time.isoformat(),
                            "last_updated_at": event_time.isoformat(),
                        }
                    )
                    tables[group_key] = current
            _reconcile_focus_state(stream_output=stream_output, event_time=event_time)
            _log_console_lines(prefix=f"group_completed:{group_key}:table", lines=table_lines)
            _log_console_lines(prefix=f"group_completed:{group_key}:club", lines=club_stats_lines)
        elif event_type == "overall_completed":
            lines = _payload_lines(payload.get("lines"))
            stream_output["overall_stats_lines"] = lines
            stream_output["overall_stats_lines_plain"] = _payload_lines(payload.get("lines_plain")) or lines
            if isinstance(payload.get("data"), dict):
                stream_output["overall_stats_data"] = payload.get("data")
            _log_console_lines(prefix="overall_completed", lines=lines)
        elif event_type == "kanaev_summary_updated":
            sheet_name = str(payload.get("sheet_name") or "")
            lines = _payload_lines(payload.get("lines"))
            if sheet_name and lines:
                summaries = stream_output.setdefault("latest_sheet_summaries", {})
                if isinstance(summaries, dict):
                    summaries[sheet_name] = {
                        "lines": lines,
                        "lines_plain": _payload_lines(payload.get("lines_plain")) or lines,
                        "data": payload.get("data") if isinstance(payload.get("data"), dict) else {},
                        "last_updated_at": event_time.isoformat(),
                    }
            _log_console_lines(prefix=f"kanaev_summary_updated:{sheet_name}", lines=lines)

    if event_type == "stream_started":
        run.status = StreamRun.Status.RUNNING
        run.started_at = run.started_at or event_time
        run.last_error = ""
        run.finished_at = None
        stream_output["competition_phase"] = stream_output.get("competition_phase") or "running"
        update_fields.update({"status", "started_at", "last_error", "finished_at"})
    elif event_type == "stream_completed":
        run.status = StreamRun.Status.SUCCESS
        run.finished_at = event_time
        stream_output["competition_phase"] = "completed"
        stream_output["status_text"] = "Соревнование завершено"
        update_fields.update({"status", "finished_at"})
    elif event_type == "stream_stopped":
        run.status = StreamRun.Status.STOPPED
        run.finished_at = event_time
        reason = str(payload.get("reason") or "").strip()
        if reason:
            run.last_error = reason
            update_fields.add("last_error")
        update_fields.update({"status", "finished_at"})
    elif event_type == "stream_error":
        run.status = StreamRun.Status.FAILED
        run.finished_at = event_time
        run.last_error = str(payload.get("error") or "stream_error")[:500]
        update_fields.update({"status", "finished_at", "last_error"})

    run.external_response_json = state
    run.save(update_fields=sorted(update_fields))


def _normalize_state(raw: object) -> dict[str, object]:
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def _payload_lines(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    lines: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).rstrip()
        if text:
            lines.append(text)
    return lines[:200]


def _is_time_value(value: object) -> bool:
    text = str(value or "").strip()
    if not text or text == "-":
        return False
    if ":" in text:
        parts = text.split(":")
        if len(parts) != 2:
            return False
        try:
            minutes = int(parts[0])
            seconds = float(parts[1].replace(",", "."))
            return minutes >= 0 and seconds >= 0
        except ValueError:
            return False
    try:
        float(text.replace(",", "."))
        return True
    except ValueError:
        return False


def _is_status_or_empty(value: object) -> bool:
    text = str(value or "").strip().upper()
    return (not text) or text == "-" or text in {"DNS", "DNF", "DSQ"}


def _extract_significant_time_updates(data: dict[str, object]) -> list[dict[str, object]]:
    updates_raw = data.get("updated_results")
    if not isinstance(updates_raw, list):
        return []
    updates: list[dict[str, object]] = []
    for item in updates_raw:
        if not isinstance(item, dict):
            continue
        sheet_name = str(item.get("sheet_name") or "").strip()
        group_name = str(item.get("group_name") or "").strip()
        if not sheet_name or not group_name:
            continue
        run2 = item.get("run2")
        run1 = item.get("run1")
        run_stage = 0
        if _is_time_value(run2):
            run_stage = 2
        elif _is_time_value(run1):
            run_stage = 1
        if run_stage not in {1, 2}:
            continue
        updates.append(
            {
                "sheet_name": sheet_name,
                "group_name": group_name,
                "group_key": f"{sheet_name}|{group_name}",
                "run_stage": run_stage,
            }
        )
    return updates


def _focus_state(stream_output: dict[str, object]) -> dict[str, object]:
    focus = stream_output.setdefault("focus_state", {})
    if not isinstance(focus, dict):
        focus = {}
        stream_output["focus_state"] = focus
    return focus


def _set_focus(
    *,
    stream_output: dict[str, object],
    sheet_name: str,
    group_key: str,
    run_stage: int,
    event_time: datetime,
) -> None:
    focus = _focus_state(stream_output)
    focus["sheet_name"] = sheet_name
    focus["group_key"] = group_key
    focus["run_stage"] = run_stage
    focus["updated_at"] = event_time.isoformat()
    stream_output["current_group_key"] = group_key
    stream_output["current_run_stage"] = run_stage


def _clear_focus_to_break(*, stream_output: dict[str, object], event_time: datetime) -> None:
    focus = _focus_state(stream_output)
    focus["group_key"] = ""
    focus["run_stage"] = 0
    focus["updated_at"] = event_time.isoformat()
    stream_output["current_group_key"] = ""
    stream_output["current_run_stage"] = 0


def _is_sheet_run_closed(stream_output: dict[str, object], *, sheet_name: str, run_stage: int) -> bool:
    tables = stream_output.get("latest_group_tables")
    if not isinstance(tables, dict):
        return False
    sheet_blocks = [
        value
        for value in tables.values()
        if isinstance(value, dict) and str(value.get("sheet_name") or "") == sheet_name
    ]
    if not sheet_blocks:
        return False

    for block in sheet_blocks:
        data = block.get("data")
        if isinstance(data, dict) and isinstance(data.get("group_table"), dict):
            data = data.get("group_table")
        rows = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            return False
        for row in rows:
            if not isinstance(row, dict):
                continue
            run1 = row.get("run1")
            run2 = row.get("run2")
            total = row.get("total")
            if run_stage == 1:
                if _is_status_or_empty(total):
                    if _is_status_or_empty(run1):
                        return False
                elif _is_status_or_empty(run1):
                    return False
            else:
                if _is_status_or_empty(total) and _is_status_or_empty(run2):
                    return False
    return True


def _update_focus_from_result_data(*, stream_output: dict[str, object], data: dict[str, object], event_time: datetime) -> None:
    updates = _extract_significant_time_updates(data)
    if not updates:
        return

    focus = _focus_state(stream_output)
    current_sheet = str(focus.get("sheet_name") or "").strip()
    current_group = str(focus.get("group_key") or "").strip()
    current_stage = _safe_int(focus.get("run_stage"), 0)

    for candidate in updates:
        candidate_sheet = str(candidate.get("sheet_name") or "")
        candidate_group = str(candidate.get("group_key") or "")
        candidate_stage = _safe_int(candidate.get("run_stage"), 0)
        if candidate_stage not in {1, 2}:
            continue

        if current_stage not in {1, 2}:
            _set_focus(
                stream_output=stream_output,
                sheet_name=candidate_sheet,
                group_key=candidate_group,
                run_stage=candidate_stage,
                event_time=event_time,
            )
            current_sheet = candidate_sheet
            current_group = candidate_group
            current_stage = candidate_stage
            continue

        if candidate_sheet == current_sheet and candidate_stage == current_stage:
            if candidate_group != current_group:
                _set_focus(
                    stream_output=stream_output,
                    sheet_name=candidate_sheet,
                    group_key=candidate_group,
                    run_stage=candidate_stage,
                    event_time=event_time,
                )
                current_group = candidate_group
            continue

        deferred = {
            "sheet_name": candidate_sheet,
            "group_key": candidate_group,
            "run_stage": candidate_stage,
            "updated_at": event_time.isoformat(),
        }
        stream_output["focus_deferred_candidate"] = deferred

    _reconcile_focus_state(stream_output=stream_output, event_time=event_time)


def _reconcile_focus_state(*, stream_output: dict[str, object], event_time: datetime) -> None:
    focus = _focus_state(stream_output)
    sheet_name = str(focus.get("sheet_name") or "").strip()
    run_stage = _safe_int(focus.get("run_stage"), 0)
    if run_stage in {1, 2} and sheet_name:
        if _is_sheet_run_closed(stream_output, sheet_name=sheet_name, run_stage=run_stage):
            _clear_focus_to_break(stream_output=stream_output, event_time=event_time)

    focus = _focus_state(stream_output)
    if _safe_int(focus.get("run_stage"), 0) in {1, 2}:
        return
    deferred = stream_output.get("focus_deferred_candidate")
    if not isinstance(deferred, dict):
        return
    candidate_stage = _safe_int(deferred.get("run_stage"), 0)
    if candidate_stage not in {1, 2}:
        return
    candidate_sheet = str(deferred.get("sheet_name") or "").strip()
    candidate_group = str(deferred.get("group_key") or "").strip()
    if not candidate_sheet or not candidate_group:
        return
    _set_focus(
        stream_output=stream_output,
        sheet_name=candidate_sheet,
        group_key=candidate_group,
        run_stage=candidate_stage,
        event_time=event_time,
    )
    stream_output.pop("focus_deferred_candidate", None)


def _initialize_focus_from_current_group(*, stream_output: dict[str, object], event_time: datetime) -> None:
    focus = _focus_state(stream_output)
    if _safe_int(focus.get("run_stage"), 0) in {1, 2}:
        return
    current_group_key = str(stream_output.get("current_group_key") or "").strip()
    if not current_group_key:
        return
    tables = stream_output.get("latest_group_tables")
    if not isinstance(tables, dict):
        return
    block = tables.get(current_group_key)
    if not isinstance(block, dict):
        return
    data = block.get("data")
    if isinstance(data, dict) and isinstance(data.get("group_table"), dict):
        data = data.get("group_table")
    if not isinstance(data, dict):
        return
    rows = data.get("rows")
    if not isinstance(rows, list):
        return
    run_stage = 2 if any(_is_time_value(row.get("run2")) for row in rows if isinstance(row, dict)) else 1
    _set_focus(
        stream_output=stream_output,
        sheet_name=str(block.get("sheet_name") or ""),
        group_key=current_group_key,
        run_stage=run_stage,
        event_time=event_time,
    )


def _upsert_completed_snapshot(
    *,
    completed_groups: dict[str, object],
    group_key: str,
    sheet_name: str,
    group_name: str,
    order_index: int,
    run_stage: int,
    data: dict[str, object],
    lines_plain: list[str],
    event_time: datetime,
    competition_format: str,
) -> None:
    key = _build_completed_group_key(group_key=group_key, run_stage=run_stage)
    existing = completed_groups.get(key, {})
    if not isinstance(existing, dict):
        existing = {}
    run_label = _build_run_label(run_stage=run_stage, competition_format=competition_format)
    completed_groups[key] = {
        "group_key": group_key,
        "sheet_name": sheet_name or str(existing.get("sheet_name") or ""),
        "group_name": group_name or str(existing.get("group_name") or ""),
        "order_index": order_index if order_index >= 0 else _safe_int(existing.get("order_index"), 0),
        "table_lines": lines_plain or _payload_lines(existing.get("table_lines")),
        "table_lines_plain": lines_plain or _payload_lines(existing.get("table_lines_plain")),
        "club_stats_lines": _payload_lines(existing.get("club_stats_lines")),
        "club_stats_lines_plain": _payload_lines(existing.get("club_stats_lines_plain")),
        "data": data if isinstance(data, dict) else (existing.get("data") if isinstance(existing.get("data"), dict) else {}),
        "run_stage": run_stage,
        "run_label": run_label,
        "option_label": _build_option_label(
            group_name=group_name or str(existing.get("group_name") or ""),
            run_label=run_label,
            competition_format=competition_format,
        ),
        "finalized_at": str(existing.get("finalized_at") or event_time.isoformat()),
        "last_updated_at": event_time.isoformat(),
        "is_finalized": True,
    }


def _parse_display_time_to_seconds(value: str) -> float | None:
    text = str(value or "").strip()
    if not text or text == "-":
        return None
    text = text.replace(",", ".")
    if ":" in text:
        parts = text.split(":")
        if len(parts) != 2:
            return None
        try:
            minutes = int(parts[0])
            seconds = float(parts[1])
            if minutes < 0 or seconds < 0:
                return None
            return (minutes * 60.0) + seconds
        except ValueError:
            return None
    try:
        parsed = float(text)
        return parsed if parsed >= 0 else None
    except ValueError:
        return None


def _run1_snapshot_sort_key(row: dict[str, object]) -> tuple[int, int, int]:
    run1_text = str(row.get("run1") or "-").strip().upper()
    run1_seconds = _parse_display_time_to_seconds(run1_text)
    start_number = _safe_int(row.get("start_number"), 0)

    note_text = str(row.get("judge_note") or "").strip().upper()
    if note_text and note_text not in {"DNS", "DNF", "DSQ"}:
        return (1, int(zlib.crc32(note_text.encode("utf-8"))), start_number)

    if run1_seconds is not None:
        return (0, int(round(run1_seconds * 1000.0)), start_number)

    status_value = str(row.get("total") or row.get("run1") or "").strip().upper()
    if status_value in {"DNS", "DNF", "DSQ"}:
        status_order = {"DNS": 0, "DNF": 1, "DSQ": 2}
        return (2, int(status_order.get(status_value, 9)), start_number)

    sheet_row = _safe_int(row.get("sheet_row"), 0)
    return (3, sheet_row, start_number)


def _build_run1_group_table_snapshot(data: dict[str, object]) -> dict[str, object]:
    if isinstance(data.get("group_table"), dict):
        data = data.get("group_table")
    if not isinstance(data, dict):
        return {}
    rows_raw = data.get("rows")
    if not isinstance(rows_raw, list):
        return {}
    run1_analytics = data.get("run1_analytics") if isinstance(data.get("run1_analytics"), dict) else {}
    run1_analytics_headers = run1_analytics.get("headers") if isinstance(run1_analytics.get("headers"), list) else []
    run1_values_by_athlete = (
        run1_analytics.get("values_by_athlete")
        if isinstance(run1_analytics.get("values_by_athlete"), dict)
        else {}
    )

    normalized_rows: list[dict[str, object]] = []
    for row in rows_raw:
        if not isinstance(row, dict):
            continue
        run1 = str(row.get("run1") or "-").strip() or "-"
        total = run1
        if str(row.get("total") or "").strip().upper() in {"DNS", "DNF", "DSQ"}:
            total = str(row.get("total") or "").strip().upper()
        normalized = dict(row)
        normalized["run2"] = "-"
        normalized["total"] = total
        normalized_rows.append(normalized)

    if not normalized_rows:
        return {}

    sorted_rows = sorted(
        normalized_rows,
        key=lambda row: _run1_snapshot_sort_key(row),
    )
    leader_time = next(
        (
            _parse_display_time_to_seconds(str(row.get("run1") or ""))
            for row in sorted_rows
            if _parse_display_time_to_seconds(str(row.get("run1") or "")) is not None
        ),
        None,
    )
    rebuilt_rows: list[dict[str, object]] = []
    for index, row in enumerate(sorted_rows, start=1):
        run1_text = str(row.get("run1") or "-").strip() or "-"
        run1_seconds = _parse_display_time_to_seconds(run1_text)
        interval = "-"
        if run1_seconds is not None and leader_time is not None:
            gap = max(run1_seconds - leader_time, 0.0)
            interval = f"+{gap:.2f}"
        rebuilt = dict(row)
        rebuilt["place"] = index
        rebuilt["interval"] = interval
        athlete_key = str(rebuilt.get("athlete_key") or "")
        analytics = rebuilt.get("analytics") if isinstance(rebuilt.get("analytics"), dict) else {}
        athlete_analytics = run1_values_by_athlete.get(athlete_key, {})
        if isinstance(athlete_analytics, dict):
            for header in run1_analytics_headers:
                header_text = str(header)
                value = athlete_analytics.get(header_text)
                if value is not None and value != "":
                    analytics[header_text] = str(value)
        if analytics:
            rebuilt["analytics"] = analytics
        rebuilt_rows.append(rebuilt)

    snapshot = dict(data)
    headers = ["место", "ст.№", "ФИО", "клуб", "1 заезд", "итог", "интервал"]
    for header in run1_analytics_headers:
        header_text = str(header)
        if header_text and header_text not in headers:
            headers.append(header_text)
    snapshot["headers"] = headers
    snapshot["rows"] = rebuilt_rows
    if run1_analytics:
        snapshot["run1_analytics"] = run1_analytics
    snapshot["lines_plain"] = _group_table_lines_from_data(snapshot)
    return snapshot


def _group_table_lines_from_data(data: dict[str, object]) -> list[str]:
    headers = data.get("headers")
    rows = data.get("rows")
    if not isinstance(headers, list) or not isinstance(rows, list):
        return []
    normalized_headers = [str(item) for item in headers]
    lines: list[str] = [" | ".join(normalized_headers)]
    for row in rows:
        if not isinstance(row, dict):
            continue
        cells: list[str] = []
        for header in normalized_headers:
            value = row.get(header)
            if value is None:
                key = str(header).lower()
                value = row.get(key)
            if value is None and isinstance(row.get("analytics"), dict):
                analytics = row.get("analytics")
                value = analytics.get(header) or analytics.get(str(header).lower())
            cells.append(str(value if value is not None and value != "" else "-"))
        lines.append(" | ".join(cells))
    return lines


def _detect_run_stage_from_payload(payload: dict[str, object]) -> int:
    data = payload.get("data")
    group_table = data.get("group_table") if isinstance(data, dict) else None
    rows = group_table.get("rows") if isinstance(group_table, dict) else None
    if not isinstance(rows, list):
        rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return 1
    if any(_safe_int(row.get("runs_count"), 2) <= 1 for row in rows if isinstance(row, dict)):
        return 1
    for row in rows:
        if not isinstance(row, dict):
            continue
        run2 = str(row.get("run2") or "").strip()
        if run2 and run2 != "-":
            return 2
    return 1


def _detect_finalized_run_stage_from_payload(payload: dict[str, object]) -> int | None:
    data = payload.get("data")
    group_table = data.get("group_table") if isinstance(data, dict) else None
    rows = group_table.get("rows") if isinstance(group_table, dict) else None
    if not isinstance(rows, list):
        rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    if any(_safe_int(row.get("runs_count"), 2) <= 1 for row in rows if isinstance(row, dict)):
        return 1

    def _filled(value: object) -> bool:
        text = str(value or "").strip()
        return bool(text) and text != "-"

    run1_values = [_filled(row.get("run1")) for row in rows if isinstance(row, dict)]
    run2_values = [_filled(row.get("run2")) for row in rows if isinstance(row, dict)]
    if not run1_values or not all(run1_values):
        return None
    if run2_values and any(run2_values) and all(run2_values):
        return 2
    return 1


def _build_completed_group_key(group_key: str, run_stage: int) -> str:
    return f"{group_key}|run{run_stage}"


def _build_run_label(*, run_stage: int, competition_format: str) -> str:
    if competition_format == "single_run":
        return "заезд 1"
    return f"заезд {run_stage}"


def _build_option_label(*, group_name: str, run_label: str, competition_format: str) -> str:
    if competition_format == "single_run":
        return group_name or "-"
    return f"{group_name or '-'} - {run_label}"


def _safe_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _log_console_lines(prefix: str, lines: list[str]) -> None:
    if not lines:
        return
    max_lines = 6
    if len(lines) <= max_lines:
        for line in lines:
            logger.info("Online Results %s: %s", prefix, line)
        return
    for line in lines[:max_lines]:
        logger.info("Online Results %s: %s", prefix, line)
    logger.info(
        "Online Results %s: ... skipped %s lines",
        prefix,
        len(lines) - max_lines,
    )


def reconcile_online_results_stream_runs() -> None:
    remote_streams = _fetch_remote_streams()
    if remote_streams is None:
        return

    canonical: list[dict[str, object]] = []
    by_source: dict[str, list[dict[str, object]]] = {}
    for item in remote_streams:
        source_id = str(item.get("source_id") or "").strip()
        if not source_id:
            continue
        by_source.setdefault(source_id, []).append(item)

    for source_id, items in by_source.items():
        items_sorted = sorted(items, key=_remote_stream_sort_key, reverse=True)
        winner = items_sorted[0]
        canonical.append(winner)
        for duplicate in items_sorted[1:]:
            duplicate_id = str(duplicate.get("stream_id") or "")
            if duplicate_id:
                _stop_remote_stream(duplicate_id)
                logger.info(
                    "Stopped duplicate remote stream during reconcile: source_id=%s stream_id=%s",
                    source_id,
                    duplicate_id,
                )

    keep_ids: set[int] = set()
    for item in canonical:
        stream_id = str(item.get("stream_id") or "").strip()
        source_id = str(item.get("source_id") or "").strip()
        if not stream_id or not source_id:
            continue
        run = StreamRun.objects.filter(stream_id=stream_id).order_by("-created_at").first()
        if run is None:
            run = StreamRun.objects.filter(source_id=source_id).order_by("-created_at").first()
        if run is None:
            run = StreamRun.objects.create(
                stream_id=stream_id,
                protocol_link=str(item.get("protocol_link") or ""),
                source_id=source_id,
                callback_url=_build_callback_url_without_request(),
            )
        run.stream_id = stream_id
        if str(item.get("protocol_link") or "").strip():
            run.protocol_link = str(item.get("protocol_link") or "").strip()
        run.source_id = source_id
        run.status = _map_remote_status_to_local(str(item.get("status") or ""))
        run.last_requested_at = timezone.now()
        run.save(
            update_fields=[
                "stream_id",
                "protocol_link",
                "source_id",
                "status",
                "last_requested_at",
                "updated_at",
            ]
        )
        keep_ids.add(run.id)
        old_duplicates = StreamRun.objects.filter(source_id=source_id).exclude(id=run.id)
        old_duplicates.delete()

    if keep_ids:
        StreamRun.objects.exclude(id__in=keep_ids).delete()
    else:
        StreamRun.objects.all().delete()


def _fetch_remote_streams() -> list[dict[str, object]] | None:
    start_url = str(getattr(settings, "ONLINE_RESULTS_STREAM_START_URL", "") or "").strip()
    if not start_url:
        return None
    headers = {"Content-Type": "application/json"}
    auth_token = str(getattr(settings, "ONLINE_RESULTS_STREAM_AUTH_TOKEN", "") or "").strip()
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    req = request.Request(url=start_url, method="GET", headers=headers)
    timeout_sec = int(getattr(settings, "ONLINE_RESULTS_STREAM_TIMEOUT_SEC", 15))
    try:
        with request.urlopen(req, timeout=timeout_sec) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else []
            if not isinstance(payload, list):
                return []
            normalized: list[dict[str, object]] = []
            for item in payload:
                if not isinstance(item, dict):
                    continue
                source_id = str(item.get("spreadsheet_id") or "").strip()
                normalized.append(
                    {
                        "stream_id": str(item.get("stream_id") or "").strip(),
                        "source_id": source_id,
                        "status": str(item.get("status") or "").strip().lower(),
                        "started_at": str(item.get("started_at") or "").strip(),
                        "last_event_at": str(item.get("last_event_at") or "").strip(),
                    }
                )
            return normalized
    except Exception as exc:
        logger.warning("Failed to reconcile stream runs from remote list: %s", exc)
        return None


def _build_callback_url_without_request() -> str:
    public_base = str(getattr(settings, "ONLINE_RESULTS_WEBHOOK_PUBLIC_BASE_URL", "") or "").strip()
    callback_path = "/integrations/online-results/webhook/"
    if public_base:
        return f"{public_base.rstrip('/')}{callback_path}"
    site_base = str(getattr(settings, "SITE_BASE_URL", "") or "").strip()
    if site_base:
        return f"{site_base.rstrip('/')}{callback_path}"
    return "http://localhost:8000/integrations/online-results/webhook/"


def _map_remote_status_to_local(status: str) -> str:
    status = (status or "").lower()
    if status == "running":
        return StreamRun.Status.RUNNING
    if status == "completed":
        return StreamRun.Status.SUCCESS
    if status == "failed":
        return StreamRun.Status.FAILED
    if status == "stopped":
        return StreamRun.Status.STOPPED
    return StreamRun.Status.PENDING


def _remote_stream_sort_key(item: dict[str, object]) -> tuple[int, float]:
    status = str(item.get("status") or "").lower()
    status_weight = 1 if status == "running" else 0
    ts_raw = str(item.get("last_event_at") or item.get("started_at") or "")
    ts = _parse_remote_ts(ts_raw)
    return status_weight, ts


def _parse_remote_ts(value: str) -> float:
    text = (value or "").strip()
    if not text:
        return 0.0
    parsed = None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
    if parsed is None:
        return 0.0
    try:
        return float(parsed.timestamp())
    except (OverflowError, OSError, ValueError):
        return 0.0


def _stop_remote_stream(stream_id: str) -> None:
    stream_id = (stream_id or "").strip()
    if not stream_id:
        return
    stop_url = _build_stop_url(stream_id)
    if not stop_url:
        return
    headers = {"Content-Type": "application/json"}
    auth_token = str(getattr(settings, "ONLINE_RESULTS_STREAM_AUTH_TOKEN", "") or "").strip()
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"
    timeout_sec = int(getattr(settings, "ONLINE_RESULTS_STREAM_TIMEOUT_SEC", 15))
    req = request.Request(url=stop_url, method="POST", data=b"", headers=headers)
    try:
        with request.urlopen(req, timeout=timeout_sec):  # noqa: S310
            return
    except Exception:
        logger.info("Failed to stop remote duplicate stream: stream_id=%s", stream_id)


def _publish_webhook_event_to_telegram(run: StreamRun, event: WebhookEvent) -> None:
    if not run.telegram_publish_enabled or run.telegram_channel_id is None:
        return
    try:
        from api.telegram_streaming import publish_stream_event_to_telegram

        publish_stream_event_to_telegram(run=run, event=event)
    except Exception:
        logger.exception(
            "Failed to publish Online Results event to Telegram: run_id=%s stream_id=%s event_id=%s",
            run.id,
            run.stream_id,
            event.id,
        )


def _enqueue_telegram_publication(event_id: int) -> None:
    if "test" in sys.argv:
        publish_online_results_event_to_telegram(event_id=event_id)
        return
    event = WebhookEvent.objects.filter(id=event_id).only("id", "stream_id", "event_type").first()
    if event is None:
        log_event("tg_enqueue_skipped", reason="event_missing", event_id=event_id)
        return
    if event.event_type not in SUPPORTED_EVENT_TYPES:
        log_event(
            "tg_enqueue_skipped",
            reason="unsupported_event_type",
            event_id=event.id,
            event_type=event.event_type,
            stream_id=event.stream_id,
        )
        return
    stream_id = str(event.stream_id or "").strip()
    if not stream_id:
        log_event("tg_enqueue_skipped", reason="empty_stream_id", event_id=event.id)
        return
    min_interval_sec = float(getattr(settings, "ONLINE_RESULTS_TELEGRAM_PUBLISH_MIN_INTERVAL_SEC", 2.0))
    if min_interval_sec < 0.5:
        min_interval_sec = 0.5
    cooldown_key = f"online_results:tg_cooldown:{stream_id}"
    cooldown_ttl = int(min_interval_sec) if min_interval_sec.is_integer() else int(min_interval_sec) + 1
    if not cache.add(cooldown_key, "1", timeout=max(1, cooldown_ttl)):
        log_event(
            "tg_enqueue_skipped",
            reason="cooldown",
            event_id=event.id,
            stream_id=stream_id,
            interval_sec=min_interval_sec,
        )
        return
    try:
        from api.tasks import publish_online_results_stream_to_telegram_task

        publish_online_results_stream_to_telegram_task.delay(stream_id)
        log_event(
            "tg_enqueue_scheduled",
            event_id=event.id,
            stream_id=stream_id,
            interval_sec=min_interval_sec,
        )
    except Exception:
        logger.exception("Failed to enqueue telegram publication task: event_id=%s", event_id)
        log_event(
            "tg_enqueue_failed_fallback_sync",
            event_id=event_id,
            stream_id=stream_id,
        )
        # Fallback for local/dev when Celery broker is unavailable.
        publish_online_results_event_to_telegram(event_id=event_id)


def publish_online_results_event_to_telegram(event_id: int) -> None:
    started = perf_counter()
    event = WebhookEvent.objects.filter(id=event_id).first()
    if event is None:
        log_event("tg_publish_event_skipped", reason="event_missing", event_id=event_id)
        return
    run = _find_stream_run_by_stream_id(event.stream_id)
    if run is None:
        log_event(
            "tg_publish_event_skipped",
            reason="run_missing",
            event_id=event.id,
            stream_id=event.stream_id,
        )
        return
    if not run.telegram_publish_enabled or run.telegram_channel_id is None:
        log_event(
            "tg_publish_event_skipped",
            reason="tg_disabled_or_channel_missing",
            event_id=event.id,
            stream_id=event.stream_id,
        )
        return
    if _has_newer_telegram_relevant_event(event=event):
        log_event(
            "tg_publish_event_skipped",
            reason="newer_relevant_event_exists",
            event_id=event.id,
            stream_id=event.stream_id,
        )
        return
    log_event(
        "tg_publish_event_started",
        event_id=event.id,
        stream_id=event.stream_id,
        event_type=event.event_type,
    )
    _publish_webhook_event_to_telegram(run=run, event=event)
    log_event(
        "tg_publish_event_finished",
        event_id=event.id,
        stream_id=event.stream_id,
        duration_ms=int((perf_counter() - started) * 1000),
    )


def _has_newer_telegram_relevant_event(event: WebhookEvent) -> bool:
    return WebhookEvent.objects.filter(
        stream_id=event.stream_id,
        id__gt=event.id,
        event_type__in=sorted(SUPPORTED_EVENT_TYPES),
    ).exists()


def publish_online_results_stream_to_telegram(stream_id: str) -> None:
    stream_id = (stream_id or "").strip()
    if not stream_id:
        return
    started = perf_counter()
    advisory_lock_acquired = _acquire_stream_publish_advisory_lock(stream_id)
    if not advisory_lock_acquired:
        log_event("tg_publish_stream_skipped", stream_id=stream_id, reason="advisory_lock_busy")
        return
    publish_lock_key = f"online_results:tg_publish_lock:{stream_id}"
    if not cache.add(publish_lock_key, "1", timeout=10):
        log_event("tg_publish_stream_skipped", stream_id=stream_id, reason="publish_lock_busy")
        _release_stream_publish_advisory_lock(stream_id)
        return
    log_event("tg_publish_stream_started", stream_id=stream_id)
    try:
        latest_event = (
            WebhookEvent.objects.filter(stream_id=stream_id, event_type__in=sorted(SUPPORTED_EVENT_TYPES))
            .order_by("-id")
            .first()
        )
        if latest_event is None:
            log_event("tg_publish_stream_finished", stream_id=stream_id, reason="no_relevant_events")
            return
        publish_online_results_event_to_telegram(event_id=latest_event.id)
        log_event(
            "tg_publish_stream_finished",
            stream_id=stream_id,
            latest_event_id=latest_event.id,
            latest_event_type=latest_event.event_type,
            duration_ms=int((perf_counter() - started) * 1000),
        )
    finally:
        cache.delete(publish_lock_key)
        _release_stream_publish_advisory_lock(stream_id)


def _acquire_stream_publish_advisory_lock(stream_id: str) -> bool:
    if connection.vendor != "postgresql":
        return True
    lock_key = _stream_publish_lock_key(stream_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_key])
        row = cursor.fetchone()
    return bool(row and row[0])


def _release_stream_publish_advisory_lock(stream_id: str) -> None:
    if connection.vendor != "postgresql":
        return
    lock_key = _stream_publish_lock_key(stream_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_key])


def _stream_publish_lock_key(stream_id: str) -> int:
    digest = hashlib.sha256(stream_id.encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], byteorder="big", signed=True)
    if value == 0:
        return 1
    return value


@contextmanager
def _stream_webhook_processing_lock(stream_id: str):
    stream_id = (stream_id or "").strip()
    if not stream_id or connection.vendor != "postgresql":
        yield
        return

    lock_key = _stream_webhook_lock_key(stream_id)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s)", [lock_key])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_key])


def _stream_webhook_lock_key(stream_id: str) -> int:
    digest = hashlib.sha256(f"webhook:{stream_id}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], byteorder="big", signed=True)
    if value == 0:
        return 7
    return value
