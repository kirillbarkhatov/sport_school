from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Sequence

from django.db import transaction
from django.utils import timezone

from school.choices import ClassCoachStatus, ClassCreationSource
from school.models import Class
from users.utils import get_class_queryset_for_user
from .models import TrainingTemplate


def _format_full_name(person) -> str:
    parts = [person.surname, person.name, person.middlename]
    return " ".join(part for part in parts if part).strip()


def _serialize_enrollments(class_instance: Class) -> tuple[list[dict], dict[int, dict]]:
    athletes: dict[int, dict] = OrderedDict()
    enrollments_payload: list[dict] = []
    for enrollment in class_instance.enrollments.all():
        athlete = enrollment.athlete
        enrollments_payload.append(
            {
                "athlete_id": athlete.id,
                "confirmed": enrollment.confirmed,
                "status": "confirmed" if enrollment.confirmed else "pending",
            }
        )
        if athlete.id not in athletes:
            person = athlete.person
            athletes[athlete.id] = {
                "id": athlete.id,
                "full_name": _format_full_name(person),
                "level": athlete.level,
                "rank": athlete.rank,
                "comment": athlete.comment,
                "groups": [
                    {
                        "id": group.id,
                        "name": group.name,
                    }
                    for group in athlete.groups_athletes.all()
                ],
                "person": {
                    "id": person.id,
                    "name": person.name,
                    "surname": person.surname,
                    "middlename": person.middlename,
                    "phone": person.phone,
                    "email": person.email,
                    "telegram": person.telegram,
                },
            }
    return enrollments_payload, athletes


def get_assistant_schedule_payload(user, limit: int | None = None) -> tuple[list[dict], list[dict]]:
    """
    Возвращает структуру с ближайшими занятиями и уникальными спортсменами
    для передачи в AI-ассистент.
    """

    classes_qs = (
        get_class_queryset_for_user(user)
        .select_related("group")
        .prefetch_related(
            "enrollments__athlete__person",
            "enrollments__athlete__groups_athletes",
        )
        .filter(date__gte=timezone.now())
        .order_by("date")
    )

    if limit is not None and limit > 0:
        classes: Sequence[Class] = list(classes_qs[:limit])
    else:
        classes = list(classes_qs)

    trainings_payload: list[dict] = []
    athletes_map: OrderedDict[int, dict] = OrderedDict()

    for class_instance in classes:
        local_dt = timezone.localtime(class_instance.date)
        enrollments_payload, enrollment_athletes = _serialize_enrollments(class_instance)

        trainings_payload.append(
            {
                "id": class_instance.id,
                "start_date": local_dt.date(),
                "start_time": local_dt.time().replace(microsecond=0),
                "start_datetime": local_dt,
                "duration_minutes": class_instance.duration,
                "location": class_instance.location,
                "location_display": class_instance.get_location_display(),
                "training_type": class_instance.training_type,
                "training_type_display": class_instance.get_training_type_display(),
                "format": class_instance.type,
                "format_display": class_instance.get_type_display(),
                "equipment": list(class_instance.equipment or []),
                "equipment_display": class_instance.get_equipment_display(),
                "comment": class_instance.comment,
                "coach_comment": class_instance.coach_comment,
                "coach_status": class_instance.coach_status,
                "coach_status_display": class_instance.get_coach_status_display(),
                "creation_source": class_instance.creation_source,
                "group": (
                    {
                        "id": class_instance.group_id,
                        "name": class_instance.group.name,
                    }
                    if class_instance.group_id and class_instance.group
                    else None
                ),
                "athletes": enrollments_payload,
            }
        )

        for athlete_id, athlete_payload in enrollment_athletes.items():
            athletes_map.setdefault(athlete_id, athlete_payload)

    return trainings_payload, list(athletes_map.values())


def ensure_week_ahead_schedule(reference_date=None) -> list[Class]:
    """Генерирует занятия по активным шаблонам на неделю вперёд."""

    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    start_date = reference_date or today
    end_date = start_date + timedelta(days=6)

    created_classes: list[Class] = []

    with transaction.atomic():
        templates = (
            TrainingTemplate.objects.filter(is_active=True)
            .select_for_update()
            .select_related("group")
            .order_by("day_of_week", "start_time")
        )

        for template in templates:
            target_date = template.next_occurrence(start_date)
            if target_date > end_date:
                continue
            if not template.applies_to_date(target_date):
                continue

            naive_start = datetime.combine(target_date, template.start_time)
            aware_start = timezone.make_aware(naive_start, tz)

            exists = Class.objects.filter(group=template.group, date=aware_start).exists()
            if exists:
                continue

            new_class = Class.objects.create(
                date=aware_start,
                duration=template.duration_minutes,
                location=template.location,
                training_type=template.training_type,
                equipment=template.default_equipment(),
                group=template.group,
                type=template.class_type,
                comment=template.comment or "Автоматически создано по шаблону",
                creation_source=ClassCreationSource.TEMPLATE,
                coach_status=ClassCoachStatus.PENDING,
            )
            created_classes.append(new_class)

    return created_classes
