import hashlib
import hmac
import json
import logging
import re
import uuid

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Max
from django.http import HttpResponseForbidden
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
from api.tasks import launch_online_results_stream_task, process_online_results_webhook_event_task
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
        if request.content_type != "application/json":
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
        form = StreamRunLaunchForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Проверьте параметры запуска потока.")
            return redirect("online-results-stream-runs")

        protocol_link: str = form.cleaned_data["protocol_link"].strip()
        callback_url = request.build_absolute_uri(reverse("online-results-webhook"))

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
