from __future__ import annotations

from typing import Any, Dict, List

from django.db import transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from school.models import Class, ClassEnrollment

from .authentication import ServiceAccountJWTAuthentication
from .models import AssistantSyncLog, AssistantUnmatchedParticipant, ServiceAccount
from .serializers import (
    TrainingAttendancePayloadSerializer,
    TrainingUpdatesPayloadSerializer,
    UnmatchedParticipantsPayloadSerializer,
)


class AssistantIntegrationPermission(permissions.BasePermission):
    """Разрешение, проверяющее что сервисный аккаунт активен."""

    def has_permission(self, request, view):
        user = request.user
        return isinstance(user, ServiceAccount) and user.is_active


class AssistantBaseView(APIView):
    authentication_classes = [ServiceAccountJWTAuthentication]
    permission_classes = [AssistantIntegrationPermission]

    def _log_incoming(
        self,
        *,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
        event_type: str,
    ) -> None:
        AssistantSyncLog.objects.create(
            service_account=self.request.user if isinstance(self.request.user, ServiceAccount) else None,
            direction=AssistantSyncLog.Direction.INCOMING,
            event_type=event_type,
            request_payload=request_payload,
            response_payload=response_payload,
        )


class AssistantTrainingUpdatesView(AssistantBaseView):
    """Обновление параметров тренировок, присланных ассистентом."""

    serializer_class = TrainingUpdatesPayloadSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        results: list[dict[str, Any]] = []
        not_found: list[int] = []

        with transaction.atomic():
            for item in payload["trainings"]:
                training_id = item["training_id"]
                try:
                    class_instance = Class.objects.select_for_update().get(pk=training_id)
                except Class.DoesNotExist:
                    not_found.append(training_id)
                    continue

                updated_fields: list[str] = []

                if "start_datetime" in item:
                    class_instance.date = item["start_datetime"]
                    updated_fields.append("date")

                if "duration_minutes" in item:
                    class_instance.duration = item["duration_minutes"]
                    updated_fields.append("duration")

                if "location" in item:
                    class_instance.location = item["location"]
                    updated_fields.append("location")

                if "training_type" in item:
                    class_instance.training_type = item["training_type"]
                    updated_fields.append("training_type")

                if "equipment" in item:
                    class_instance.equipment = item["equipment"]
                    updated_fields.append("equipment")

                if "comment" in item:
                    class_instance.assistant_comment = item["comment"]
                    updated_fields.append("assistant_comment")

                if updated_fields:
                    class_instance.save(update_fields=list(dict.fromkeys(updated_fields)))

                results.append(
                    {
                        "training_id": training_id,
                        "updated_fields": updated_fields,
                    }
                )

        response_payload = {"updated": results, "not_found": not_found}
        self._log_incoming(
            request_payload=payload,
            response_payload=response_payload,
            event_type="trainings.update",
        )
        status_code = status.HTTP_207_MULTI_STATUS if not_found else status.HTTP_200_OK
        return Response(response_payload, status=status_code)


class AssistantTrainingAttendanceView(AssistantBaseView):
    """Приём статусов посещения участников тренировки."""

    serializer_class = TrainingAttendancePayloadSerializer
    STATUS_MAPPING = {
        "confirmed": ClassEnrollment.ASSISTANT_STATUS_CONFIRMED,
        "declined": ClassEnrollment.ASSISTANT_STATUS_DECLINED,
        "pending": ClassEnrollment.ASSISTANT_STATUS_PENDING,
    }

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        training_id = payload["training_id"]
        try:
            class_instance = Class.objects.get(pk=training_id)
        except Class.DoesNotExist:
            response_payload = {
                "updated": [],
                "missing_training": training_id,
                "not_matched": payload["attendance"],
            }
            self._log_incoming(
                request_payload=payload,
                response_payload=response_payload,
                event_type="trainings.attendance",
            )
            return Response(response_payload, status=status.HTTP_404_NOT_FOUND)

        updated: list[dict[str, Any]] = []
        not_matched: list[dict[str, Any]] = []

        with transaction.atomic():
            for attendee in payload["attendance"]:
                enrollment = self._resolve_enrollment(class_instance, attendee)
                if not enrollment:
                    not_matched.append(attendee)
                    continue

                status_code = attendee["status"]
                assistant_status = self.STATUS_MAPPING.get(status_code, ClassEnrollment.ASSISTANT_STATUS_UNKNOWN)
                enrollment.assistant_status = assistant_status

                if status_code == "confirmed":
                    enrollment.confirmed = True
                elif status_code == "declined":
                    enrollment.confirmed = False

                if attendee.get("comment"):
                    enrollment.assistant_comment = attendee["comment"]
                elif enrollment.assistant_comment and status_code == "pending":
                    # keep previous comment unless overwritten
                    pass
                if attendee.get("comment") == "":
                    enrollment.assistant_comment = ""

                enrollment.save(
                    update_fields=[
                        "assistant_status",
                        "assistant_comment",
                        "confirmed",
                    ],
                )

                updated.append(
                    {
                        "enrollment_id": enrollment.id,
                        "athlete_id": enrollment.athlete_id,
                        "assistant_status": assistant_status,
                        "confirmed": enrollment.confirmed,
                    }
                )

        response_payload = {"updated": updated, "not_matched": not_matched}
        self._log_incoming(
            request_payload=payload,
            response_payload=response_payload,
            event_type="trainings.attendance",
        )
        status_code = status.HTTP_207_MULTI_STATUS if not_matched else status.HTTP_200_OK
        return Response(response_payload, status=status_code)

    def _resolve_enrollment(self, class_instance: Class, attendee: dict[str, Any]) -> ClassEnrollment | None:
        athlete_id = attendee.get("athlete_id")
        phone = attendee.get("athlete_phone")

        enrollment_qs = class_instance.enrollments.select_related("athlete__person")

        if athlete_id:
            enrollment = enrollment_qs.filter(athlete_id=athlete_id).first()
            if enrollment:
                return enrollment

        if phone:
            normalized_phone = self._normalize_phone(phone)
            if normalized_phone:
                enrollment = enrollment_qs.filter(athlete__person__phone=normalized_phone).first()
                if enrollment:
                    return enrollment

        return None

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        digits = "".join(filter(str.isdigit, phone or ""))
        if digits.startswith("8") and len(digits) == 11:
            digits = "7" + digits[1:]
        if digits and not digits.startswith("+"):
            digits = "+" + digits
        return digits


class AssistantUnmatchedParticipantsView(AssistantBaseView):
    """Сохранение списка участников, которых не удалось сопоставить."""

    serializer_class = UnmatchedParticipantsPayloadSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data)
        serializer.is_valid(raise_exception=True)
        payload = serializer.validated_data

        service_account = request.user if isinstance(request.user, ServiceAccount) else None
        created: list[int] = []

        with transaction.atomic():
            for participant in payload["participants"]:
                entry = AssistantUnmatchedParticipant.objects.create(
                    service_account=service_account,
                    training_id=payload.get("training_id"),
                    full_name=participant.get("full_name", ""),
                    phone=participant.get("phone", ""),
                    comment=participant.get("comment", ""),
                    raw_payload=participant.get("raw") or participant,
                )
                created.append(entry.id)

        response_payload = {"created": created}
        self._log_incoming(
            request_payload=payload,
            response_payload=response_payload,
            event_type="trainings.unmatched_participants",
        )
        return Response(response_payload, status=status.HTTP_201_CREATED)
