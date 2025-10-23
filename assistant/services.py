from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, time, timedelta
from typing import Any, Dict, Iterable, Optional

from django.conf import settings
from django.db.models import Prefetch
from django.utils import timezone

from school.models import Class, FamilyMember, Person
from school.choices import TrainingEquipment, TrainingKind, TrainingLocation
from .models import ServiceAccount


def is_integration_enabled() -> bool:
    return bool(getattr(settings, "AI_ASSISTANT_ENABLED", False))


def get_service_account_for_outgoing() -> Optional[ServiceAccount]:
    slug = getattr(settings, "AI_ASSISTANT_SERVICE_ACCOUNT_SLUG", None)
    if not slug:
        return None
    try:
        return ServiceAccount.objects.get(slug=slug, is_active=True)
    except ServiceAccount.DoesNotExist:
        return None


def _format_person_name(person: Person) -> str:
    parts = [person.surname, person.name, person.middlename]
    return " ".join(filter(None, parts)).strip()


def build_members_payload() -> dict[str, Any]:
    """Возвращает список членов клуба с привязкой к семьям."""

    persons = (
        Person.objects.select_related("athlete")
        .prefetch_related(
            Prefetch(
                "familymember_set",
                queryset=FamilyMember.objects.select_related("family"),
            ),
            "families",
        )
        .order_by("surname", "name")
    )

    members: list[dict[str, Any]] = []

    for person in persons:
        athlete = getattr(person, "athlete", None)
        families_map: OrderedDict[int, dict[str, Any]] = OrderedDict()

        for fm in person.familymember_set.all():
            family = fm.family
            if family:
                families_map[family.id] = {
                    "id": family.id,
                    "name": family.family_name,
                    "relation": fm.relation,
                    "status": family.status,
                }

        for family in person.families.all():
            families_map.setdefault(
                family.id,
                {
                    "id": family.id,
                    "name": family.family_name,
                    "relation": "contact",
                    "status": family.status,
                },
            )

        members.append(
            {
                "person_id": person.id,
                "full_name": _format_person_name(person),
                "first_name": person.name,
                "last_name": person.surname,
                "middle_name": person.middlename,
                "phone": person.phone,
                "email": person.email,
                "is_athlete": bool(athlete),
                "athlete": {
                    "athlete_id": athlete.id,
                    "level": athlete.level,
                    "rank": athlete.rank,
                    "comment": athlete.comment,
                }
                if athlete
                else None,
                "families": list(families_map.values()),
            }
        )

    return {"members": members}


def build_taxonomy_payload() -> dict[str, list[dict[str, str]]]:
    def _serialize(choices: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
        return [{"value": value, "label": label} for value, label in choices]

    return {
        "training_types": _serialize(TrainingKind.choices),
        "equipment": _serialize(TrainingEquipment.choices),
        "locations": _serialize(TrainingLocation.choices),
    }


def build_upcoming_trainings_payload() -> dict[str, Any]:
    """Возвращает занятия на сегодня и завтра с участниками."""

    tz = timezone.get_current_timezone()
    start_date = timezone.localdate()
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end_dt = timezone.make_aware(
        datetime.combine(start_date + timedelta(days=2), time.min),
        tz,
    )

    classes = (
        Class.objects.filter(date__gte=start_dt, date__lt=end_dt)
        .select_related("group")
        .prefetch_related(
            "enrollments__athlete__person",
            "enrollments__athlete__groups_athletes",
        )
        .order_by("date")
    )

    trainings: list[dict[str, Any]] = []
    for class_instance in classes:
        local_dt = timezone.localtime(class_instance.date, tz)
        enrollment_payload: list[dict[str, Any]] = []
        for enrollment in class_instance.enrollments.all():
            athlete = enrollment.athlete
            person = athlete.person
            enrollment_payload.append(
                {
                    "enrollment_id": enrollment.id,
                    "athlete_id": athlete.id,
                    "full_name": _format_person_name(person),
                    "phone": person.phone,
                    "confirmed": enrollment.confirmed,
                    "assistant_status": getattr(enrollment, "assistant_status", "unknown"),
                    "assistant_comment": getattr(enrollment, "assistant_comment", ""),
                }
            )

        trainings.append(
            {
                "training_id": class_instance.id,
                "start_datetime": local_dt.isoformat(),
                "duration_minutes": class_instance.duration,
                "location": class_instance.location,
                "location_display": class_instance.get_location_display(),
                "training_type": class_instance.training_type,
                "training_type_display": class_instance.get_training_type_display(),
                "equipment": list(class_instance.equipment or []),
                "equipment_display": class_instance.get_equipment_display(),
                "group": (
                    {
                        "id": class_instance.group_id,
                        "name": class_instance.group.name,
                    }
                    if class_instance.group_id and class_instance.group
                    else None
                ),
                "format": class_instance.type,
                "format_display": class_instance.get_type_display(),
                "comment": class_instance.comment,
                "coach_comment": class_instance.coach_comment,
                "assistant_comment": getattr(class_instance, "assistant_comment", ""),
                "attendance": enrollment_payload,
            }
        )

    return {"trainings": trainings}
