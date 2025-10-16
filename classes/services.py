from __future__ import annotations

from collections import OrderedDict
from typing import Sequence

from django.utils import timezone

from school.models import Class
from users.utils import get_class_queryset_for_user


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
                "training_type": class_instance.training_type,
                "format": class_instance.type,
                "format_display": class_instance.get_type_display(),
                "equipment": list(class_instance.equipment or []),
                "comment": class_instance.comment,
                "group": {
                    "id": class_instance.group_id,
                    "name": class_instance.group.name,
                },
                "athletes": enrollments_payload,
            }
        )

        for athlete_id, athlete_payload in enrollment_athletes.items():
            athletes_map.setdefault(athlete_id, athlete_payload)

    return trainings_payload, list(athletes_map.values())
