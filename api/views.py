import hashlib
import hmac
import json
import logging
import re
import secrets
import uuid
from datetime import datetime, timedelta
from urllib import error, request
from urllib.parse import quote, urljoin, urlsplit

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db import IntegrityError
from django.db.models import Count, Max
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from django.views import View
from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from api.forms import StreamRunLaunchForm
from api.models import PublicStreamAccess, StreamRun, WebhookEvent
from api.tasks import (
    launch_online_results_stream_task,
    process_online_results_webhook_event_task,
    stop_online_results_stream_task,
)
from api.services import launch_online_results_stream, normalize_source_id, reset_online_results_stream_state
from api.telegram_streaming import disable_stream_telegram_publication, enable_stream_telegram_publication
from api.telemetry import log_event
from bot.models import TelegramChat, TelegramParticipant
from school.models import Person
from users.constants import ADMIN_GROUP_NAME, MANAGER_GROUP_NAME
from users.mixins import ApprovedUserRequiredMixin
from users.models import User
from classes.services import get_assistant_schedule_payload
from school.choices import TrainingEquipment, TrainingKind, TrainingLocation
from .serializers import (
    AssistantAthleteSerializer,
    AssistantTrainingSerializer,
    PersonSerializer,
    UserSerializer,
    WhatsAppChatSyncSerializer,
)

logger = logging.getLogger(__name__)
SIGNATURE_PATTERN = re.compile(r"^sha256=[0-9a-f]{64}$")
PUBLIC_STREAM_LINK_TTL = timedelta(days=2)


def _telegram_channels_queryset():
    return (
        TelegramChat.objects.filter(
            type__in=[TelegramChat.ChatType.CHANNEL, TelegramChat.ChatType.SUPERGROUP],
            participants__is_bot=True,
            participants__status__in=[
                TelegramParticipant.MemberStatus.ADMIN,
                TelegramParticipant.MemberStatus.CREATOR,
            ],
        )
        .order_by("title", "chat_id")
        .distinct()
    )


def _can_manage_online_results(user: User) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.groups.filter(name__in={ADMIN_GROUP_NAME, MANAGER_GROUP_NAME}).exists()


def _build_online_results_callback_url(request) -> str:
    callback_path = reverse("online-results-webhook")
    public_base = settings.ONLINE_RESULTS_WEBHOOK_PUBLIC_BASE_URL
    if public_base:
        return urljoin(f"{public_base.rstrip('/')}/", callback_path.lstrip("/"))
    return request.build_absolute_uri(callback_path)


def _create_public_stream_access(stream_run: StreamRun) -> PublicStreamAccess:
    PublicStreamAccess.objects.filter(stream_run=stream_run, is_active=True).update(is_active=False)
    expires_at = timezone.now() + PUBLIC_STREAM_LINK_TTL
    for _ in range(5):
        token = secrets.token_urlsafe(24)
        try:
            return PublicStreamAccess.objects.create(
                stream_run=stream_run,
                token=token,
                expires_at=expires_at,
                is_active=True,
            )
        except IntegrityError:
            continue
    raise RuntimeError("Не удалось сгенерировать токен публичного доступа к трансляции.")


def _ensure_active_public_stream_access(stream_run: StreamRun) -> PublicStreamAccess:
    active = (
        PublicStreamAccess.objects.filter(
            stream_run=stream_run,
            is_active=True,
            expires_at__gt=timezone.now(),
        )
        .order_by("-created_at")
        .first()
    )
    if active is not None:
        return active
    return _create_public_stream_access(stream_run)


def _extract_competition_title_from_athlete_key(athlete_key: object) -> str:
    if not isinstance(athlete_key, str):
        return ""
    parts = [part.strip() for part in athlete_key.split("|")]
    if len(parts) >= 2 and parts[1]:
        return parts[1]
    return ""


def _extract_competition_title(stream_output: dict[str, object]) -> str:
    if not isinstance(stream_output, dict):
        return ""

    for root_key in ("last_tick", "last_result_data", "last_event"):
        root = stream_output.get(root_key)
        if not isinstance(root, dict):
            continue
        for updates_key in ("updated_results",):
            updates = root.get(updates_key)
            if not isinstance(updates, list):
                continue
            for item in updates:
                if not isinstance(item, dict):
                    continue
                title = _extract_competition_title_from_athlete_key(item.get("athlete_key"))
                if title:
                    return title
    return ""


def _live_payload_from_run(run: StreamRun, group_key: str = "") -> dict[str, object]:
    output = run.external_response_json.get("stream_output", {}) if isinstance(run.external_response_json, dict) else {}
    if not isinstance(output, dict):
        output = {}

    completed_groups_raw = output.get("completed_groups", {})
    latest_group_tables = output.get("latest_group_tables", {})
    if not isinstance(completed_groups_raw, dict):
        completed_groups_raw = {}
    if not isinstance(latest_group_tables, dict):
        latest_group_tables = {}

    competition_format = str(output.get("competition_format") or "two_run")
    completed_items: list[dict[str, object]] = []
    for key, value in completed_groups_raw.items():
        if not isinstance(value, dict):
            continue
        run_stage = int(value.get("run_stage") or 1)
        group_name = str(value.get("group_name") or "")
        run_label = str(value.get("run_label") or ("заезд 1" if competition_format == "single_run" else f"заезд {run_stage}"))
        option_label = str(value.get("option_label") or (group_name if competition_format == "single_run" else f"{group_name} - {run_label}"))
        completed_items.append(
            {
                "group_key": key,
                "base_group_key": str(value.get("group_key") or key),
                "sheet_name": str(value.get("sheet_name") or ""),
                "group_name": group_name,
                "order_index": int(value.get("order_index") or 0),
                "is_finalized": bool(value.get("is_finalized", True)),
                "last_updated_at": str(value.get("last_updated_at") or value.get("finalized_at") or ""),
                "finalized_at": str(value.get("finalized_at") or ""),
                "run_stage": run_stage,
                "run_label": run_label,
                "option_label": option_label,
            }
        )
    completed_items.sort(
        key=lambda item: (
            int(item.get("order_index") or 0),
            int(item.get("run_stage") or 0),
            str(item.get("group_name") or "").lower(),
        )
    )
    all_groups_items: list[dict[str, object]] = []
    for key, value in latest_group_tables.items():
        if not isinstance(value, dict):
            continue
        all_groups_items.append(
            {
                "group_key": key,
                "sheet_name": str(value.get("sheet_name") or ""),
                "group_name": str(value.get("group_name") or ""),
                "order_index": int(value.get("order_index") or 0),
                "is_finalized": bool(value.get("is_finalized")),
                "last_updated_at": str(value.get("last_updated_at") or ""),
                "data": value.get("data") if isinstance(value.get("data"), dict) else {},
            }
        )
    all_groups_items.sort(
        key=lambda item: (
            int(item.get("order_index") or 0),
            str(item.get("sheet_name") or "").lower(),
            str(item.get("group_name") or "").lower(),
        )
    )

    selected_group = {}
    if group_key:
        maybe = latest_group_tables.get(group_key)
        if isinstance(maybe, dict):
            selected_group = maybe
        if not selected_group:
            maybe = completed_groups_raw.get(group_key)
            if isinstance(maybe, dict):
                selected_group = maybe

    current_group = {}
    focus_state = output.get("focus_state") if isinstance(output.get("focus_state"), dict) else {}
    current_group_key = str(focus_state.get("group_key") or output.get("current_group_key") or "")
    if current_group_key:
        maybe = latest_group_tables.get(current_group_key)
        if isinstance(maybe, dict):
            current_group = maybe

    teams: set[str] = set()
    payload_teams = output.get("teams")
    if isinstance(payload_teams, list):
        for team in payload_teams:
            team_name = str(team or "").strip()
            if team_name:
                teams.add(team_name)
    for block in list(latest_group_tables.values()) + list(completed_groups_raw.values()):
        if not isinstance(block, dict):
            continue
        data = block.get("data")
        if isinstance(data, dict) and isinstance(data.get("group_table"), dict):
            data = data.get("group_table")
        rows = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            club = str(row.get("club") or "").strip()
            if club:
                teams.add(club)
    sorted_teams = sorted(teams, key=str.lower)
    default_teams = [team for team in sorted_teams if ("канаев" in team.lower()) or ("kanaev" in team.lower())]
    competition_title = str(output.get("competition_title") or "") or _extract_competition_title(output)
    start_forecast = output.get("start_forecast")
    if not isinstance(start_forecast, dict):
        start_forecast = {}
    forecast_rows = start_forecast.get("rows")
    if not isinstance(forecast_rows, list):
        forecast_rows = []
    competition_phase = str(output.get("competition_phase") or "")
    if not competition_phase:
        if run.status == StreamRun.Status.SUCCESS:
            competition_phase = "completed"
        else:
            competition_phase = "running"
    status_text = str(output.get("status_text") or "")

    return {
        "stream": {
            "id": run.id,
            "stream_id": run.stream_id,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else "",
            "finished_at": run.finished_at.isoformat() if run.finished_at else "",
            "last_error": run.last_error,
        },
        "selected_group_key": group_key,
        "current_group_key": current_group_key,
        "current_group": current_group,
        "selected_group": selected_group,
        "completed_groups": completed_items,
        "all_groups": all_groups_items,
        "overall_stats_lines": output.get("overall_stats_lines_plain") or output.get("overall_stats_lines") or [],
        "overall_stats_data": output.get("overall_stats_data") if isinstance(output.get("overall_stats_data"), dict) else {},
        "last_tick": output.get("last_tick") if isinstance(output.get("last_tick"), dict) else {},
        "last_event": output.get("last_event") if isinstance(output.get("last_event"), dict) else {},
        "last_warning": output.get("last_warning") if isinstance(output.get("last_warning"), dict) else {},
        "start_forecast": {"rows": forecast_rows, "updated_at": str(start_forecast.get("updated_at") or "")},
        "competition_phase": competition_phase,
        "competition_format": competition_format,
        "status_text": status_text,
        "teams": sorted_teams,
        "default_selected_teams": default_teams,
        "competition_title": competition_title,
    }


def _last_webhook_received_at(stream_id: str) -> datetime | None:
    if not stream_id:
        return None
    return WebhookEvent.objects.filter(stream_id=stream_id).aggregate(last_at=Max("received_at")).get("last_at")


def _build_remote_stream_state_url(stream_id: str) -> str:
    start_url = str(getattr(settings, "ONLINE_RESULTS_STREAM_START_URL", "") or "").strip()
    if not start_url or not stream_id:
        return ""
    suffix = "/v1/streams"
    if start_url.endswith(suffix):
        return f"{start_url}/{quote(stream_id, safe='')}"
    parsed = urlsplit(start_url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    path = parsed.path or ""
    marker = path.find(suffix)
    if marker < 0:
        return ""
    base_path = path[: marker + len(suffix)]
    return f"{parsed.scheme}://{parsed.netloc}{base_path}/{quote(stream_id, safe='')}"


def _probe_online_results_stream_state(stream_id: str) -> dict[str, object]:
    state_url = _build_remote_stream_state_url(stream_id)
    if not state_url:
        return {"ok": False, "reason": "state_url_not_configured"}

    headers = {"Content-Type": "application/json"}
    auth_token = str(getattr(settings, "ONLINE_RESULTS_STREAM_AUTH_TOKEN", "") or "").strip()
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    req = request.Request(url=state_url, method="GET", headers=headers)
    timeout_sec = int(getattr(settings, "ONLINE_RESULTS_STREAM_TIMEOUT_SEC", 15))
    try:
        with request.urlopen(req, timeout=timeout_sec) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            if not isinstance(payload, dict):
                payload = {}
            return {
                "ok": True,
                "found": True,
                "status": str(payload.get("status") or "").lower(),
            }
    except error.HTTPError as exc:
        if exc.code == 404:
            return {"ok": True, "found": False, "status": "not_found"}
        logger.warning("online_results_state_probe_http_error stream_id=%s code=%s", stream_id, exc.code)
        return {"ok": False, "reason": f"http_{exc.code}"}
    except (error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        logger.warning("online_results_state_probe_transport_error stream_id=%s error=%s", stream_id, exc)
        return {"ok": False, "reason": str(exc)[:200]}


def _should_recover_stream(run: StreamRun) -> bool:
    now = timezone.now()
    if run.status == StreamRun.Status.STOPPED:
        # Do not auto-recover streams explicitly stopped by operator action.
        if "manual_stop" in str(run.last_error or ""):
            return False
        return True
    if run.status == StreamRun.Status.FAILED:
        return True
    if run.status == StreamRun.Status.PENDING:
        age = (now - run.created_at).total_seconds()
        return age >= settings.ONLINE_RESULTS_STREAM_PENDING_TIMEOUT_SEC
    if run.status != StreamRun.Status.RUNNING:
        return False
    remote_state = _probe_online_results_stream_state(run.stream_id)
    if bool(remote_state.get("ok")):
        if not bool(remote_state.get("found")):
            return True
        remote_status = str(remote_state.get("status") or "").lower()
        if remote_status == "running":
            return False
        if remote_status in {"completed", "success"}:
            return False
        if remote_status in {"failed", "stopped"}:
            return True
    last_event_at = _last_webhook_received_at(run.stream_id) or run.started_at or run.created_at
    if last_event_at is None:
        return False
    silence_sec = (now - last_event_at).total_seconds()
    return silence_sec >= settings.ONLINE_RESULTS_STREAM_RESUME_STALE_SEC


def _find_recent_recovery_candidate(source_run: StreamRun) -> StreamRun | None:
    source_id = str(source_run.source_id or "").strip() or normalize_source_id(str(source_run.protocol_link or ""))
    if not source_id:
        return None
    cooldown_sec = int(getattr(settings, "ONLINE_RESULTS_STREAM_RECOVERY_COOLDOWN_SEC", 30))
    if cooldown_sec <= 0:
        return None
    cutoff = timezone.now() - timedelta(seconds=cooldown_sec)
    return (
        StreamRun.objects.filter(
            source_id=source_id,
            status__in=[StreamRun.Status.PENDING, StreamRun.Status.RUNNING],
            created_at__gte=cutoff,
        )
        .exclude(id=source_run.id)
        .order_by("-created_at")
        .first()
    )


def _create_and_start_recovery_run(
    source_run: StreamRun,
    callback_url: str,
    created_by: User | None,
) -> StreamRun:
    stream_run = StreamRun.objects.create(
        stream_id=f"pending-{uuid.uuid4().hex[:10]}",
        protocol_link=source_run.protocol_link,
        source_id=source_run.source_id or normalize_source_id(source_run.protocol_link),
        stream_type=source_run.stream_type,
        status=StreamRun.Status.PENDING,
        launch_payload_json=source_run.launch_payload_json or {},
        callback_url=callback_url,
        created_by=created_by if created_by and created_by.is_authenticated else None,
    )
    launch_online_results_stream(stream_run.id)
    stream_run.refresh_from_db()
    return stream_run


def _resolve_or_recover_stream_run(
    *,
    request,
    stream_id: str,
    allow_recover: bool,
    created_by: User | None = None,
) -> StreamRun | None:
    run = StreamRun.objects.filter(stream_id=stream_id).order_by("-created_at").first()
    if run is None:
        return None
    if not allow_recover:
        return run

    # If a newer running run for same link already exists, switch to it.
    if run.source_id:
        newer_running = (
            StreamRun.objects.filter(source_id=run.source_id, status=StreamRun.Status.RUNNING)
            .exclude(id=run.id)
            .order_by("-created_at")
            .first()
        )
        if newer_running:
            return newer_running

    if not _should_recover_stream(run):
        return run

    recent_recovery = _find_recent_recovery_candidate(run)
    if recent_recovery is not None:
        return recent_recovery

    callback_url = _build_online_results_callback_url(request)
    recovered = _create_and_start_recovery_run(source_run=run, callback_url=callback_url, created_by=created_by)
    return recovered


class PersonViewSet(viewsets.ModelViewSet):
    """Вьюсет для курсов"""

    model = Person
    serializer_class = PersonSerializer
    queryset = Person.objects.all()
    permission_classes = [IsAuthenticated]


class UserViewSet(viewsets.ModelViewSet):
    """Вьюсет для Пользователя"""

    model = User
    serializer_class = UserSerializer
    queryset = User.objects.all()
    permission_classes = [IsAuthenticated]


class WhatsAppChatSyncView(APIView):
    """Приём данных о составе чатов WhatsApp"""

    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = WhatsAppChatSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chat = serializer.save()
        data = serializer.validated_data
        active_members = chat.members.filter(is_active=True).count()
        return Response(
            {
                "chat_id": chat.id,
                "group_name": chat.name,
                "synced_at": data["synced_at"],
                "processed_members": len(data["members"]),
                "active_member_count": active_members,
            },
            status=status.HTTP_200_OK,
        )


class AssistantUpcomingTrainingsView(APIView):
    """Возвращает ближайшие тренировки и список спортсменов для AI-ассистента."""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        limit_param = request.query_params.get("limit")
        limit: int | None = 5

        if limit_param is not None:
            try:
                limit_value = int(limit_param)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Параметр limit должен быть целым числом."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if limit_value <= 0:
                return Response(
                    {"detail": "Параметр limit должен быть положительным."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            limit = limit_value

        trainings_payload, athletes_payload = get_assistant_schedule_payload(
            request.user,
            limit=limit,
        )

        trainings = AssistantTrainingSerializer(trainings_payload, many=True).data
        athletes = AssistantAthleteSerializer(athletes_payload, many=True).data

        return Response(
            {
                "trainings": trainings,
                "athletes": athletes,
                "defaults": {
                    "training_types": [
                        {"value": value, "label": label}
                        for value, label in TrainingKind.choices
                    ],
                    "equipment": [
                        {"value": value, "label": label}
                        for value, label in TrainingEquipment.choices
                    ],
                    "locations": [
                        {"value": value, "label": label}
                        for value, label in TrainingLocation.choices
                    ],
                },
            },
            status=status.HTTP_200_OK,
        )


@method_decorator(csrf_exempt, name="dispatch")
class OnlineResultsWebhookView(APIView):
    permission_classes = [AllowAny]
    http_method_names = ["post"]

    def post(self, request, *args, **kwargs):
        content_type = (request.content_type or "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            return Response(
                {"detail": "Content-Type must be application/json."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_body = request.body
        payload_hash = hashlib.sha256(raw_body).hexdigest()
        signature = request.headers.get("X-Online-Results-Signature")
        secret = settings.ONLINE_RESULTS_WEBHOOK_SECRET

        if not secret:
            logger.error("ONLINE_RESULTS_WEBHOOK_SECRET is not configured")
            return Response(status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if not signature or SIGNATURE_PATTERN.fullmatch(signature) is None:
            logger.warning(
                "Online Results webhook rejected: missing_or_invalid_signature_header payload_hash=%s",
                payload_hash,
            )
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        signature_value = signature.removeprefix("sha256=")
        expected_signature = hmac.new(
            key=secret.encode("utf-8"),
            msg=raw_body,
            digestmod=hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(signature_value, expected_signature):
            logger.warning(
                "Online Results webhook rejected: signature_mismatch payload_hash=%s",
                payload_hash,
            )
            return Response(status=status.HTTP_401_UNAUTHORIZED)

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning(
                "Online Results webhook rejected: invalid_json payload_hash=%s",
                payload_hash,
            )
            return Response(
                {"detail": "Invalid JSON payload."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not isinstance(payload, dict):
            logger.warning(
                "Online Results webhook rejected: json_is_not_object payload_hash=%s",
                payload_hash,
            )
            return Response(
                {"detail": "JSON payload must be an object."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        event_time = self._parse_event_time(payload.get("event_time"))
        stream_id = str(payload.get("stream_id") or "")
        event_type = str(payload.get("event_type") or "")

        event, created = WebhookEvent.objects.get_or_create(
            payload_hash=payload_hash,
            defaults={
                "stream_id": stream_id,
                "event_type": event_type,
                "event_time": event_time,
                "payload_json": payload,
            },
        )
        if not created:
            logger.info(
                "Online Results webhook duplicate ignored: payload_hash=%s",
                payload_hash,
            )
            log_event(
                "webhook_duplicate_ignored",
                payload_hash=payload_hash,
                stream_id=stream_id,
                event_type=event_type,
            )
            return Response(status=status.HTTP_200_OK)

        transaction.on_commit(lambda: self._enqueue_processing(event.id))
        log_event(
            "webhook_received",
            event_id=event.id,
            stream_id=stream_id,
            event_type=event_type,
            payload_hash=payload_hash,
        )
        logger.info(
            "Online Results webhook accepted: event_id=%s stream_id=%s event_type=%s payload_hash=%s",
            event.id,
            stream_id,
            event_type,
            payload_hash,
        )
        return Response(status=status.HTTP_200_OK)

    @staticmethod
    def _parse_event_time(value: object):
        if not isinstance(value, str):
            return None
        parsed = parse_datetime(value)
        if parsed is None:
            return None
        if timezone.is_naive(parsed):
            return timezone.make_aware(parsed, timezone.get_default_timezone())
        return parsed

    @staticmethod
    def _enqueue_processing(event_id: int) -> None:
        try:
            process_online_results_webhook_event_task.delay(event_id)
            log_event("webhook_processing_enqueued", event_id=event_id)
        except Exception:
            logger.exception(
                "Online Results webhook enqueue failed: event_id=%s",
                event_id,
            )
            log_event("webhook_processing_enqueue_failed", event_id=event_id)


class OnlineResultsStreamRunsView(ApprovedUserRequiredMixin, View):
    template_name = "api/online_results_stream_runs.html"

    def dispatch(self, request, *args, **kwargs):
        if not _can_manage_online_results(request.user):
            return HttpResponseForbidden("Недостаточно прав.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        form = StreamRunLaunchForm()
        telegram_channels = list(_telegram_channels_queryset())
        status_filter = (request.GET.get("status") or "").strip()
        stream_filter = (request.GET.get("stream_id") or "").strip()

        queryset = StreamRun.objects.select_related("created_by", "telegram_channel").order_by("-last_requested_at", "-created_at")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if stream_filter:
            queryset = queryset.filter(stream_id__icontains=stream_filter)

        paginator = Paginator(queryset, 20)
        page_obj = paginator.get_page(request.GET.get("page"))

        stream_ids = [item.stream_id for item in page_obj.object_list if item.stream_id]
        events_map = {
            item["stream_id"]: item
            for item in (
                WebhookEvent.objects.filter(stream_id__in=stream_ids)
                .values("stream_id")
                .annotate(events_count=Count("id"), last_event_received_at=Max("received_at"))
            )
        }

        runs_payload: list[dict[str, object]] = []
        for run in page_obj.object_list:
            event_stats = events_map.get(run.stream_id, {})
            public_link = _ensure_active_public_stream_access(run)
            output = run.external_response_json.get("stream_output", {}) if isinstance(run.external_response_json, dict) else {}
            if not isinstance(output, dict):
                output = {}
            competition_title = str(output.get("competition_title") or "").strip() or _extract_competition_title(output)
            runs_payload.append(
                {
                    "run": run,
                    "competition_title": competition_title or "Без названия",
                    "events_count": event_stats.get("events_count", 0),
                    "last_event_received_at": event_stats.get("last_event_received_at"),
                    "public_url": (
                        request.build_absolute_uri(
                            reverse("online-results-live-public", kwargs={"token": public_link.token})
                        )
                        if public_link
                        else ""
                    ),
                    "public_expires_at": public_link.expires_at if public_link else None,
                    "telegram_channel_id": run.telegram_channel_id,
                }
            )

        return render(
            request,
            self.template_name,
            {
                "form": form,
                "runs_payload": runs_payload,
                "page_obj": page_obj,
                "status_filter": status_filter,
                "stream_filter": stream_filter,
                "status_choices": StreamRun.Status.choices,
                "telegram_channels": telegram_channels,
            },
        )

    def post(self, request, *args, **kwargs):
        action = (request.POST.get("action") or "start").strip()
        if action == "stop":
            run_id_raw = (request.POST.get("run_id") or "").strip()
            if not run_id_raw.isdigit():
                messages.error(request, "Некорректный идентификатор потока.")
                return redirect("online-results-stream-runs")

            run = StreamRun.objects.filter(id=int(run_id_raw)).first()
            if run is None:
                messages.error(request, "Поток не найден.")
                return redirect("online-results-stream-runs")
            if run.status != StreamRun.Status.RUNNING:
                messages.warning(request, "Поток уже не выполняется.")
                return redirect("online-results-stream-runs")

            transaction.on_commit(
                lambda: stop_online_results_stream_task.delay(
                    run.id,
                    reason=f"manual_stop_by_user_{request.user.id}",
                )
            )
            messages.success(request, f"Остановка потока {run.stream_id} поставлена в очередь.")
            return redirect("online-results-stream-runs")
        if action in {"telegram_enable", "telegram_disable"}:
            run_id_raw = (request.POST.get("run_id") or "").strip()
            if not run_id_raw.isdigit():
                messages.error(request, "Некорректный идентификатор потока.")
                return redirect("online-results-stream-runs")
            run = StreamRun.objects.filter(id=int(run_id_raw)).first()
            if run is None:
                messages.error(request, "Поток не найден.")
                return redirect("online-results-stream-runs")

            if action == "telegram_disable":
                try:
                    disable_stream_telegram_publication(run=run)
                except Exception:
                    logger.exception("Failed to disable telegram publication: run_id=%s", run.id)
                    messages.error(request, "Не удалось отключить Telegram-публикацию.")
                    return redirect("online-results-stream-runs")
                messages.success(request, "Telegram-публикация отключена.")
                return redirect("online-results-stream-runs")

            channel_id_raw = (request.POST.get("telegram_channel_id") or "").strip()
            if not channel_id_raw.isdigit():
                messages.error(request, "Выберите Telegram-группу/канал.")
                return redirect("online-results-stream-runs")
            try:
                enable_stream_telegram_publication(run=run, channel_id=int(channel_id_raw))
            except Exception:
                logger.exception("Failed to enable telegram publication: run_id=%s", run.id)
                messages.error(request, "Не удалось включить Telegram-публикацию.")
                return redirect("online-results-stream-runs")
            messages.success(request, "Telegram-публикация включена с текущего состояния соревнований.")
            return redirect("online-results-stream-runs")

        form = StreamRunLaunchForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Проверьте параметры запуска потока.")
            return redirect("online-results-stream-runs")

        protocol_link: str = form.cleaned_data["protocol_link"].strip()
        source_id = normalize_source_id(protocol_link)
        callback_url = _build_online_results_callback_url(request)
        with transaction.atomic():
            stream_run = StreamRun.objects.select_for_update().filter(source_id=source_id).first()
            created = stream_run is None
            if created:
                stream_run = StreamRun.objects.create(
                    stream_id=f"pending-{uuid.uuid4().hex[:10]}",
                    protocol_link=protocol_link,
                    source_id=source_id,
                    callback_url=callback_url,
                    created_by=request.user,
                    status=StreamRun.Status.PENDING,
                    last_requested_at=timezone.now(),
                )
                public_access = _create_public_stream_access(stream_run)
            else:
                stream_run.protocol_link = protocol_link
                stream_run.callback_url = callback_url
                stream_run.last_requested_at = timezone.now()
                if stream_run.status != StreamRun.Status.RUNNING:
                    stream_run.status = StreamRun.Status.PENDING
                stream_run.save(
                    update_fields=[
                        "protocol_link",
                        "callback_url",
                        "status",
                        "last_requested_at",
                        "updated_at",
                    ]
                )
                public_access = (
                    PublicStreamAccess.objects.filter(stream_run=stream_run, is_active=True, expires_at__gt=timezone.now())
                    .order_by("-created_at")
                    .first()
                )
                if public_access is None:
                    public_access = _create_public_stream_access(stream_run)

        should_launch = stream_run.status != StreamRun.Status.RUNNING or stream_run.stream_id.startswith("pending-")
        if should_launch:
            transaction.on_commit(lambda: launch_online_results_stream_task.delay(stream_run.id))
            launch_note = "Поток поставлен в очередь."
        else:
            launch_note = "Поток уже выполняется, строка обновлена."
        messages.success(
            request,
            f"{launch_note} Публичная ссылка активна до "
            f"{timezone.localtime(public_access.expires_at).strftime('%d.%m.%Y %H:%M')}.",
        )
        return redirect("online-results-stream-runs")


class OnlineResultsWebhookEventsView(ApprovedUserRequiredMixin, View):
    template_name = "api/online_results_webhook_events.html"

    def dispatch(self, request, *args, **kwargs):
        if not _can_manage_online_results(request.user):
            return HttpResponseForbidden("Недостаточно прав.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        stream_filter = (request.GET.get("stream_id") or "").strip()
        event_type_filter = (request.GET.get("event_type") or "").strip()
        date_from = (request.GET.get("date_from") or "").strip()
        date_to = (request.GET.get("date_to") or "").strip()
        selected_event_id = request.GET.get("event_id")

        queryset = WebhookEvent.objects.order_by("-received_at")
        if stream_filter:
            queryset = queryset.filter(stream_id__icontains=stream_filter)
        if event_type_filter:
            queryset = queryset.filter(event_type__icontains=event_type_filter)
        if date_from:
            queryset = queryset.filter(received_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(received_at__date__lte=date_to)

        paginator = Paginator(queryset, 50)
        page_obj = paginator.get_page(request.GET.get("page"))

        selected_event = None
        if selected_event_id and str(selected_event_id).isdigit():
            selected_event = WebhookEvent.objects.filter(id=int(selected_event_id)).first()

        event_type_options = (
            WebhookEvent.objects.exclude(event_type="")
            .values_list("event_type", flat=True)
            .distinct()
            .order_by("event_type")
        )

        return render(
            request,
            self.template_name,
            {
                "page_obj": page_obj,
                "stream_filter": stream_filter,
                "event_type_filter": event_type_filter,
                "date_from": date_from,
                "date_to": date_to,
                "selected_event": selected_event,
                "event_type_options": event_type_options,
            },
        )


class OnlineResultsCompetitionLiveView(ApprovedUserRequiredMixin, View):
    template_name = "api/online_results_live.html"

    def dispatch(self, request, *args, **kwargs):
        if not _can_manage_online_results(request.user):
            return HttpResponseForbidden("Недостаточно прав.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        stream_id = (request.GET.get("stream_id") or "").strip()
        latest_run = StreamRun.objects.order_by("-last_requested_at", "-created_at").first()
        if not stream_id and latest_run:
            stream_id = latest_run.stream_id
        competition_title = ""
        run_for_title = StreamRun.objects.filter(stream_id=stream_id).order_by("-created_at").first() if stream_id else latest_run
        if run_for_title:
            payload = _live_payload_from_run(run=run_for_title)
            competition_title = str(payload.get("competition_title") or "")
        return render(
            request,
            self.template_name,
            {
                "stream_id": stream_id,
                "latest_run": latest_run,
                "state_url": reverse("online-results-live-state"),
                "is_public": False,
                "page_heading": "Online Results: Текущие соревнования",
                "competition_title": competition_title,
            },
        )


class OnlineResultsCompetitionLiveStateView(ApprovedUserRequiredMixin, View):
    def dispatch(self, request, *args, **kwargs):
        if not _can_manage_online_results(request.user):
            return HttpResponseForbidden("Недостаточно прав.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        stream_id = (request.GET.get("stream_id") or "").strip()
        if not stream_id:
            return JsonResponse({"detail": "stream_id is required"}, status=400)

        run = _resolve_or_recover_stream_run(
            request=request,
            stream_id=stream_id,
            allow_recover=True,
            created_by=request.user,
        )
        if run is None:
            return JsonResponse({"detail": "stream not found"}, status=404)
        group_key = (request.GET.get("group_key") or "").strip()
        payload = _live_payload_from_run(run=run, group_key=group_key)
        payload["resolved_stream_id"] = run.stream_id
        return JsonResponse(payload)


class OnlineResultsCompetitionSoftRefreshView(ApprovedUserRequiredMixin, View):
    http_method_names = ["post"]

    def dispatch(self, request, *args, **kwargs):
        if not _can_manage_online_results(request.user):
            return HttpResponseForbidden("Недостаточно прав.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        stream_id = (request.POST.get("stream_id") or request.GET.get("stream_id") or "").strip()
        if not stream_id:
            return JsonResponse({"detail": "stream_id is required"}, status=400)

        run = _resolve_or_recover_stream_run(
            request=request,
            stream_id=stream_id,
            allow_recover=True,
            created_by=request.user,
        )
        if run is None:
            return JsonResponse({"detail": "stream not found"}, status=404)

        payload = _live_payload_from_run(run=run)
        payload["resolved_stream_id"] = run.stream_id
        payload["soft_refreshed"] = True
        return JsonResponse(payload)


class OnlineResultsCompetitionHardRefreshView(ApprovedUserRequiredMixin, View):
    http_method_names = ["post"]

    def dispatch(self, request, *args, **kwargs):
        if not _can_manage_online_results(request.user):
            return HttpResponseForbidden("Недостаточно прав.")
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        stream_id = (request.POST.get("stream_id") or request.GET.get("stream_id") or "").strip()
        if not stream_id:
            return JsonResponse({"detail": "stream_id is required"}, status=400)

        run = _resolve_or_recover_stream_run(
            request=request,
            stream_id=stream_id,
            allow_recover=False,
            created_by=request.user,
        )
        if run is None:
            return JsonResponse({"detail": "stream not found"}, status=404)

        try:
            refreshed_run = reset_online_results_stream_state(run_id=run.id, requested_by=request.user)
        except RuntimeError as exc:
            return JsonResponse({"detail": str(exc)}, status=502)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=404)

        payload = _live_payload_from_run(run=refreshed_run)
        payload["resolved_stream_id"] = refreshed_run.stream_id
        payload["hard_refreshed"] = True
        payload["previous_stream_id"] = run.stream_id
        return JsonResponse(payload)


class OnlineResultsCompetitionLivePublicView(View):
    template_name = "api/online_results_live.html"

    def get(self, request, token: str, *args, **kwargs):
        access = (
            PublicStreamAccess.objects.select_related("stream_run")
            .filter(token=token, is_active=True)
            .order_by("-created_at")
            .first()
        )
        if access is None:
            return HttpResponseForbidden("Публичная ссылка не найдена.")
        if access.is_expired:
            return HttpResponseForbidden("Срок действия публичной ссылки истек.")
        payload = _live_payload_from_run(run=access.stream_run)
        competition_title = str(payload.get("competition_title") or "").strip() or "Текущие соревнования"
        return render(
            request,
            self.template_name,
            {
                "stream_id": access.stream_run.stream_id,
                "latest_run": access.stream_run,
                "state_url": reverse("online-results-live-public-state", kwargs={"token": token}),
                "is_public": True,
                "public_expires_at": access.expires_at,
                "page_heading": competition_title,
                "competition_title": competition_title,
                "hide_navigation": True,
                "app_brand_title": "Онлайн протокол",
            },
        )


class OnlineResultsCompetitionLivePublicStateView(View):
    def get(self, request, token: str, *args, **kwargs):
        access = (
            PublicStreamAccess.objects.select_related("stream_run")
            .filter(token=token, is_active=True)
            .order_by("-created_at")
            .first()
        )
        if access is None:
            return JsonResponse({"detail": "public link not found"}, status=404)
        if access.is_expired:
            return JsonResponse({"detail": "public link expired"}, status=410)

        run = _resolve_or_recover_stream_run(
            request=request,
            stream_id=access.stream_run.stream_id,
            allow_recover=True,
            created_by=access.stream_run.created_by,
        )
        if run is None:
            return JsonResponse({"detail": "stream not found"}, status=404)
        if run.id != access.stream_run_id:
            access.stream_run = run
            access.save(update_fields=["stream_run"])

        group_key = (request.GET.get("group_key") or "").strip()
        payload = _live_payload_from_run(run=run, group_key=group_key)
        payload["resolved_stream_id"] = run.stream_id
        payload["public"] = {
            "expires_at": access.expires_at.isoformat(),
            "is_public": True,
        }
        return JsonResponse(payload)
