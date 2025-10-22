from __future__ import annotations

from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from school.choices import TrainingEquipment, TrainingKind, TrainingLocation


class TrainingUpdateItemSerializer(serializers.Serializer):
    training_id = serializers.IntegerField()
    start_datetime = serializers.DateTimeField(required=False)
    duration_minutes = serializers.IntegerField(min_value=1, required=False)
    location = serializers.ChoiceField(
        choices=TrainingLocation.values,
        required=False,
    )
    training_type = serializers.ChoiceField(
        choices=TrainingKind.values,
        required=False,
    )
    equipment = serializers.ListField(
        child=serializers.ChoiceField(choices=TrainingEquipment.values),
        required=False,
    )
    comment = serializers.CharField(required=False, allow_blank=True, max_length=500)


class TrainingUpdatesPayloadSerializer(serializers.Serializer):
    trainings = TrainingUpdateItemSerializer(many=True)


class AttendanceItemSerializer(serializers.Serializer):
    athlete_id = serializers.IntegerField(required=False)
    athlete_phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    status = serializers.ChoiceField(
        choices=("confirmed", "declined", "pending"),
        help_text="confirmed — придёт, declined — не придёт, pending — решение неизвестно",
    )
    comment = serializers.CharField(required=False, allow_blank=True, max_length=500)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs.get("athlete_id") and not attrs.get("athlete_phone"):
            raise serializers.ValidationError(
                "Требуется указать athlete_id или athlete_phone для участника.",
            )
        return attrs


class TrainingAttendancePayloadSerializer(serializers.Serializer):
    training_id = serializers.IntegerField()
    attendance = AttendanceItemSerializer(many=True)


class UnmatchedParticipantItemSerializer(serializers.Serializer):
    full_name = serializers.CharField(required=False, allow_blank=True, max_length=255)
    phone = serializers.CharField(required=False, allow_blank=True, max_length=32)
    comment = serializers.CharField(required=False, allow_blank=True, max_length=500)
    raw = serializers.DictField(
        child=serializers.JSONField(),
        required=False,
        help_text="Произвольные данные, присланные ассистентом",
    )


class UnmatchedParticipantsPayloadSerializer(serializers.Serializer):
    training_id = serializers.IntegerField(required=False)
    participants = UnmatchedParticipantItemSerializer(many=True)
