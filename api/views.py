import hashlib
import hmac
import json
import logging
import re
import uuid
from urllib.parse import urljoin

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
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
from api.models import StreamRun, WebhookEvent
from api.tasks import (
    launch_online_results_stream_task,
    process_online_results_webhook_event_task,
    stop_online_results_stream_task,
)
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
            return Response(status=status.HTTP_200_OK)

        transaction.on_commit(lambda: self._enqueue_processing(event.id))
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
        except Exception:
            logger.exception(
                "Online Results webhook enqueue failed: event_id=%s",
                event_id,
            )


class OnlineResultsStreamRunsView(ApprovedUserRequiredMixin, View):
    template_name = "api/online_results_stream_runs.html"

    def dispatch(self, request, *args, **kwargs):
        if not _can_manage_online_results(request.user):
            return HttpResponseForbidden("Недостаточно прав.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        form = StreamRunLaunchForm()
        status_filter = (request.GET.get("status") or "").strip()
        stream_filter = (request.GET.get("stream_id") or "").strip()

        queryset = StreamRun.objects.select_related("created_by").order_by("-created_at")
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
            runs_payload.append(
                {
                    "run": run,
                    "events_count": event_stats.get("events_count", 0),
                    "last_event_received_at": event_stats.get("last_event_received_at"),
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

        form = StreamRunLaunchForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Проверьте параметры запуска потока.")
            return redirect("online-results-stream-runs")

        protocol_link: str = form.cleaned_data["protocol_link"].strip()
        callback_url = _build_online_results_callback_url(request)

        stream_run = StreamRun.objects.create(
            stream_id=f"pending-{uuid.uuid4().hex[:10]}",
            protocol_link=protocol_link,
            callback_url=callback_url,
            created_by=request.user,
        )
        transaction.on_commit(lambda: launch_online_results_stream_task.delay(stream_run.id))
        messages.success(
            request,
            "Поток поставлен в очередь на запуск.",
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
        latest_run = StreamRun.objects.order_by("-created_at").first()
        if not stream_id and latest_run:
            stream_id = latest_run.stream_id
        return render(
            request,
            self.template_name,
            {
                "stream_id": stream_id,
                "latest_run": latest_run,
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

        run = StreamRun.objects.filter(stream_id=stream_id).order_by("-created_at").first()
        if run is None:
            return JsonResponse({"detail": "stream not found"}, status=404)

        output = run.external_response_json.get("stream_output", {}) if isinstance(run.external_response_json, dict) else {}
        if not isinstance(output, dict):
            output = {}

        completed_groups_raw = output.get("completed_groups", {})
        completed_items: list[dict[str, object]] = []
        if isinstance(completed_groups_raw, dict):
            for key, value in completed_groups_raw.items():
                if not isinstance(value, dict):
                    continue
                completed_items.append(
                    {
                        "group_key": key,
                        "sheet_name": str(value.get("sheet_name") or ""),
                        "group_name": str(value.get("group_name") or ""),
                        "is_finalized": bool(value.get("is_finalized", True)),
                        "last_updated_at": str(value.get("last_updated_at") or value.get("finalized_at") or ""),
                        "finalized_at": str(value.get("finalized_at") or ""),
                    }
                )
        completed_items.sort(key=lambda item: str(item.get("last_updated_at") or ""), reverse=True)

        group_key = (request.GET.get("group_key") or "").strip()
        if not group_key:
            if isinstance(output.get("current_group_key"), str) and output.get("current_group_key"):
                group_key = str(output.get("current_group_key"))
            elif completed_items:
                group_key = str(completed_items[0]["group_key"])

        latest_group_tables = output.get("latest_group_tables", {})
        selected_group = {}
        if isinstance(latest_group_tables, dict) and group_key:
            selected_group = latest_group_tables.get(group_key) if isinstance(latest_group_tables.get(group_key), dict) else {}
        if (not selected_group) and isinstance(completed_groups_raw, dict) and group_key:
            selected_group = completed_groups_raw.get(group_key) if isinstance(completed_groups_raw.get(group_key), dict) else {}

        current_group = {}
        current_group_key = str(output.get("current_group_key") or "")
        if current_group_key and isinstance(latest_group_tables, dict):
            maybe = latest_group_tables.get(current_group_key)
            if isinstance(maybe, dict):
                current_group = maybe

        payload = {
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
            "overall_stats_lines": output.get("overall_stats_lines_plain") or output.get("overall_stats_lines") or [],
            "overall_stats_data": output.get("overall_stats_data") if isinstance(output.get("overall_stats_data"), dict) else {},
            "last_tick": output.get("last_tick") if isinstance(output.get("last_tick"), dict) else {},
            "last_event": output.get("last_event") if isinstance(output.get("last_event"), dict) else {},
        }
        return JsonResponse(payload)
