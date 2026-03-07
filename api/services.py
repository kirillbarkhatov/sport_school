import logging
from json import JSONDecodeError
import json
from urllib import error, request
from urllib.parse import quote

from django.conf import settings
from django.db.models import QuerySet
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

    run = _find_stream_run_by_stream_id(event.stream_id)
    if run:
        _apply_webhook_event_to_stream_run(run=run, event=event)
    else:
        logger.warning(
            "StreamRun not found for webhook event: event_id=%s stream_id=%s event_type=%s",
            event.id,
            event.stream_id,
            event.event_type,
        )

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


def _build_stop_url(stream_id: str) -> str:
    template = getattr(settings, "ONLINE_RESULTS_STREAM_STOP_URL_TEMPLATE", "").strip()
    if template and "{stream_id}" in template:
        return template.replace("{stream_id}", quote(stream_id, safe=""))

    start_url = settings.ONLINE_RESULTS_STREAM_START_URL.strip()
    suffix = "/v1/streams"
    if start_url.endswith(suffix):
        return f"{start_url}/{quote(stream_id, safe='')}/stop"
    return ""


def _stop_duplicate_running_streams(current_run: StreamRun) -> None:
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
            stream_output["status_text"] = str(payload.get("status_text") or "")
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
                            "lines": lines_plain,
                            "lines_plain": lines_plain,
                            "data": data if isinstance(data, dict) else {},
                            "is_finalized": bool(item.get("is_finalized")),
                            "last_updated_at": event_time.isoformat(),
                        }
                        run_stage = int(item.get("run_stage") or 0)
                        if run_stage in {1, 2}:
                            completed_groups = stream_output.setdefault("completed_groups", {})
                            if isinstance(completed_groups, dict):
                                completed_groups[_build_completed_group_key(group_key=group_key, run_stage=run_stage)] = {
                                    "group_key": group_key,
                                    "sheet_name": str(item.get("sheet_name") or ""),
                                    "group_name": str(item.get("group_name") or ""),
                                    "table_lines": lines_plain,
                                    "table_lines_plain": lines_plain,
                                    "club_stats_lines": [],
                                    "club_stats_lines_plain": [],
                                    "data": {"group_table": data} if isinstance(data, dict) else {},
                                    "run_stage": run_stage,
                                    "run_label": f"заезд {run_stage}",
                                    "option_label": f"{str(item.get('group_name') or '')} - заезд {run_stage}",
                                    "finalized_at": event_time.isoformat(),
                                    "last_updated_at": event_time.isoformat(),
                                    "is_finalized": True,
                                }
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
            stream_output["status_text"] = str(payload.get("status_text") or stream_output.get("status_text") or "")
        elif event_type == "tick":
            stream_output["last_tick"] = {
                "ts": str(payload.get("ts") or ""),
                "changed_count": int(payload.get("changed_count") or 0),
            }
            stream_output["competition_phase"] = str(payload.get("competition_phase") or stream_output.get("competition_phase") or "running")
            stream_output["status_text"] = str(payload.get("status_text") or stream_output.get("status_text") or "")
        elif event_type == "result_updated":
            lines = _payload_lines(payload.get("lines"))
            if lines:
                stream_output["last_result_lines"] = lines
                _log_console_lines(prefix="result_updated", lines=lines)
            data = payload.get("data")
            if isinstance(data, dict):
                stream_output["last_result_data"] = data
        elif event_type == "group_table_updated":
            group_key = str(payload.get("group_key") or "")
            lines = _payload_lines(payload.get("lines"))
            data = payload.get("data")
            if group_key and (lines or isinstance(data, dict)):
                tables = stream_output.setdefault("latest_group_tables", {})
                if isinstance(tables, dict):
                    tables[group_key] = {
                        "sheet_name": str(payload.get("sheet_name") or ""),
                        "group_name": str(payload.get("group_name") or ""),
                        "lines": lines,
                        "lines_plain": _payload_lines(payload.get("lines_plain")) or lines,
                        "data": data if isinstance(data, dict) else {},
                        "is_finalized": bool(
                            tables.get(group_key, {}).get("is_finalized")
                            if isinstance(tables.get(group_key), dict)
                            else False
                        ),
                        "last_updated_at": event_time.isoformat(),
                    }
                    # If finalized group is updated later, sync the matching "group+run" snapshot too.
                    run_stage = _detect_run_stage_from_payload(payload)
                    completed_key = _build_completed_group_key(group_key=group_key, run_stage=run_stage)
                    completed_groups = stream_output.setdefault("completed_groups", {})
                    if isinstance(completed_groups, dict) and isinstance(completed_groups.get(completed_key), dict):
                        completed_item = completed_groups.get(completed_key, {})
                        completed_item.update(
                            {
                                "sheet_name": str(payload.get("sheet_name") or completed_item.get("sheet_name") or ""),
                                "group_name": str(payload.get("group_name") or completed_item.get("group_name") or ""),
                                "table_lines": lines or completed_item.get("table_lines") or [],
                                "table_lines_plain": _payload_lines(payload.get("lines_plain")) or lines,
                                "data": data if isinstance(data, dict) else (completed_item.get("data") or {}),
                                "run_stage": run_stage,
                                "run_label": f"заезд {run_stage}",
                                "option_label": f"{str(payload.get('group_name') or completed_item.get('group_name') or '')} - заезд {run_stage}",
                                "last_updated_at": event_time.isoformat(),
                            }
                        )
                        completed_groups[completed_key] = completed_item
                _log_console_lines(prefix=f"group_table_updated:{group_key}", lines=lines)
                stream_output["current_group_key"] = group_key
                inferred_final_stage = _detect_finalized_run_stage_from_payload(payload)
                if inferred_final_stage in {1, 2}:
                    completed_key = _build_completed_group_key(group_key=group_key, run_stage=inferred_final_stage)
                    completed_groups = stream_output.setdefault("completed_groups", {})
                    if isinstance(completed_groups, dict):
                        existing = completed_groups.get(completed_key, {})
                        if not isinstance(existing, dict):
                            existing = {}
                        completed_groups[completed_key] = {
                            "group_key": group_key,
                            "sheet_name": str(payload.get("sheet_name") or existing.get("sheet_name") or ""),
                            "group_name": str(payload.get("group_name") or existing.get("group_name") or ""),
                            "table_lines": lines or existing.get("table_lines") or [],
                            "table_lines_plain": _payload_lines(payload.get("lines_plain")) or lines,
                            "club_stats_lines": existing.get("club_stats_lines") or [],
                            "club_stats_lines_plain": existing.get("club_stats_lines_plain") or [],
                            "data": data if isinstance(data, dict) else (existing.get("data") or {}),
                            "run_stage": inferred_final_stage,
                            "run_label": f"заезд {inferred_final_stage}",
                            "option_label": (
                                f"{str(payload.get('group_name') or existing.get('group_name') or '')} - "
                                f"заезд {inferred_final_stage}"
                            ),
                            "finalized_at": str(existing.get("finalized_at") or event_time.isoformat()),
                            "last_updated_at": event_time.isoformat(),
                            "is_finalized": True,
                        }
        elif event_type == "group_completed":
            group_key = str(payload.get("group_key") or "")
            table_lines = _payload_lines(payload.get("table_lines"))
            club_stats_lines = _payload_lines(payload.get("club_stats_lines"))
            if group_key:
                run_stage = _detect_run_stage_from_payload(payload)
                completed_key = _build_completed_group_key(group_key=group_key, run_stage=run_stage)
                completed_groups = stream_output.setdefault("completed_groups", {})
                if isinstance(completed_groups, dict):
                    completed_groups[completed_key] = {
                        "group_key": group_key,
                        "sheet_name": str(payload.get("sheet_name") or ""),
                        "group_name": str(payload.get("group_name") or ""),
                        "table_lines": table_lines,
                        "table_lines_plain": _payload_lines(payload.get("table_lines_plain")) or table_lines,
                        "club_stats_lines": club_stats_lines,
                        "club_stats_lines_plain": _payload_lines(payload.get("club_stats_lines_plain")) or club_stats_lines,
                        "data": payload.get("data") if isinstance(payload.get("data"), dict) else {},
                        "run_stage": run_stage,
                        "run_label": f"заезд {run_stage}",
                        "option_label": f"{str(payload.get('group_name') or '')} - заезд {run_stage}",
                        "finalized_at": event_time.isoformat(),
                        "last_updated_at": event_time.isoformat(),
                        "is_finalized": True,
                    }
                tables = stream_output.setdefault("latest_group_tables", {})
                if isinstance(tables, dict):
                    current = tables.get(group_key, {}) if isinstance(tables.get(group_key), dict) else {}
                    current.update(
                        {
                            "sheet_name": str(payload.get("sheet_name") or current.get("sheet_name") or ""),
                            "group_name": str(payload.get("group_name") or current.get("group_name") or ""),
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


def _detect_run_stage_from_payload(payload: dict[str, object]) -> int:
    data = payload.get("data")
    group_table = data.get("group_table") if isinstance(data, dict) else None
    rows = group_table.get("rows") if isinstance(group_table, dict) else None
    if not isinstance(rows, list):
        rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list):
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


def _log_console_lines(prefix: str, lines: list[str]) -> None:
    if not lines:
        return
    for line in lines:
        logger.info("Online Results %s: %s", prefix, line)
