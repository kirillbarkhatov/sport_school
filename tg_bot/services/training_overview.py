from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Dict, Iterable, Sequence

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from school.choices import ClassCoachStatus, TrainingKind
from school.models import Athlete, Class, ClassEnrollment
from users.models import User
from users.utils import get_athlete_queryset_for_user


TRAINING_TYPE_EMOJI = {
    TrainingKind.FITNESS: "💪",
    TrainingKind.ROLLERS: "🛼",
    TrainingKind.ICE: "⛸️",
    TrainingKind.SKI: "🎿",
    TrainingKind.SKI_BEGINNERS: "🎿",
    TrainingKind.SKI_DRILLS: "⛷️",
    TrainingKind.SLALOM: "⛷️",
    TrainingKind.GIANT_SLALOM: "⛷️",
    TrainingKind.BIKE: "🚴",
    TrainingKind.SKITECH: "🤖",
    TrainingKind.TRAMPOLINE: "🤸",
    TrainingKind.MANEZH: "🏟️",
}

COACH_STATUS_HINTS = {
    ClassCoachStatus.PENDING: "⏳ Тренер ещё не подтвердил тренировку.",
    ClassCoachStatus.PLANNED: "✅ Тренировка подтверждена тренером.",
    ClassCoachStatus.CANCELLED: "❌ Тренировка отменена тренером.",
}

ATTENDANCE_STATUS_ICONS = {
    "confirmed": "✅",
    "declined": "❌",
    "unknown": "⏳",
}

ATTENDANCE_STATUS_DESCRIPTIONS = {
    "confirmed": "подтвердил участие",
    "declined": "не сможет участвовать",
    "unknown": "ещё не ответил",
}


@dataclasses.dataclass(slots=True)
class AthleteAttendance:
    athlete_id: int
    person_id: int
    short_name: str
    full_name: str
    status_key: str
    status_text: str


@dataclasses.dataclass(slots=True)
class TrainingSummary:
    class_id: int
    start: datetime
    duration_minutes: int
    training_type: str
    training_type_display: str
    location: str
    location_display: str
    group_name: str
    coach_status: str
    coach_status_display: str
    equipment: Sequence[str]
    equipment_display: str
    comment: str | None
    coach_comment: str | None
    emoji: str
    athletes: Sequence[AthleteAttendance]


def _format_person_name(person) -> str:
    parts = [person.surname, person.name, person.middlename]
    return " ".join(part for part in parts if part).strip() or "Без имени"


def _person_short_name(person) -> str:
    return person.name or person.surname or "Без имени"


def _attendance_status(enrollment: ClassEnrollment) -> tuple[str, str]:
    status = enrollment.assistant_status
    if status == ClassEnrollment.ASSISTANT_STATUS_DECLINED:
        key = "declined"
    if status == ClassEnrollment.ASSISTANT_STATUS_CONFIRMED or enrollment.confirmed:
        key = "confirmed"
    else:
        key = "unknown"
    return key, ATTENDANCE_STATUS_DESCRIPTIONS[key]


def _resolve_training_emoji(training_type: str) -> str:
    return TRAINING_TYPE_EMOJI.get(training_type, "🏋️")


def _collect_accessible_athletes(user: User) -> Dict[int, Athlete]:
    athletes = (
        get_athlete_queryset_for_user(user)
        .select_related("person")
        .order_by("person__surname", "person__name")
    )
    return {athlete.id: athlete for athlete in athletes}


def _serialize_training(
    class_instance: Class,
    athlete_map: Dict[int, Athlete],
) -> TrainingSummary | None:
    local_start = timezone.localtime(class_instance.date)
    attendees: list[AthleteAttendance] = []
    seen_athlete_ids: set[int] = set()

    enrollments: Iterable[ClassEnrollment] = class_instance.enrollments.all()
    for enrollment in enrollments:
        athlete = enrollment.athlete
        mapped_athlete = athlete_map.get(athlete.id)
        if not mapped_athlete:
            continue

        person = mapped_athlete.person
        status_key, status_text = _attendance_status(enrollment)
        attendees.append(
            AthleteAttendance(
                athlete_id=athlete.id,
                person_id=person.id,
                short_name=_person_short_name(person),
                full_name=_format_person_name(person),
                status_key=status_key,
                status_text=status_text,
            )
        )
        seen_athlete_ids.add(athlete.id)

    group = class_instance.group
    if group:
        group_athletes_manager = getattr(group, "athletes", None)
        if group_athletes_manager is not None:
            for group_athlete in group_athletes_manager.all():
                if group_athlete.id in seen_athlete_ids:
                    continue
                mapped_athlete = athlete_map.get(group_athlete.id)
                if not mapped_athlete:
                    continue
                person = mapped_athlete.person
                attendees.append(
                    AthleteAttendance(
                        athlete_id=group_athlete.id,
                        person_id=person.id,
                        short_name=_person_short_name(person),
                        full_name=_format_person_name(person),
                        status_key="unknown",
                        status_text=ATTENDANCE_STATUS_DESCRIPTIONS["unknown"],
                    )
                )
                seen_athlete_ids.add(group_athlete.id)

    equipment_display = class_instance.get_equipment_display()
    return TrainingSummary(
        class_id=class_instance.id,
        start=local_start,
        duration_minutes=class_instance.duration,
        training_type=class_instance.training_type,
        training_type_display=class_instance.get_training_type_display(),
        location=class_instance.location,
        location_display=class_instance.get_location_display(),
        group_name=class_instance.group.name,
        coach_status=class_instance.coach_status,
        coach_status_display=class_instance.get_coach_status_display(),
        equipment=tuple(class_instance.equipment or []),
        equipment_display=equipment_display,
        comment=class_instance.comment,
        coach_comment=class_instance.coach_comment,
        emoji=_resolve_training_emoji(class_instance.training_type),
        athletes=tuple(attendees),
    )


def _build_classes_queryset(user: User):
    athlete_ids = list(
        get_athlete_queryset_for_user(user).values_list("id", flat=True)
    )
    if not athlete_ids:
        return Class.objects.none()

    return (
        Class.objects.filter(
            Q(enrollments__athlete_id__in=athlete_ids)
            | Q(group__athletes__id__in=athlete_ids)
        )
        .select_related("group")
        .prefetch_related(
            "enrollments__athlete__person",
            "group__athletes__person",
        )
        .distinct()
    )


def get_upcoming_trainings_for_user(
    user: User,
    limit: int | None = None,
    until: datetime | None = None,
) -> list[TrainingSummary]:
    now = timezone.now()
    classes_qs = _build_classes_queryset(user).filter(date__gte=now).order_by("date")

    if until is not None:
        classes_qs = classes_qs.filter(date__lte=until)

    if limit is not None and limit > 0:
        classes: Sequence[Class] = list(classes_qs[:limit])
    else:
        classes = list(classes_qs)

    if not classes:
        return []

    athlete_map = _collect_accessible_athletes(user)
    summaries: list[TrainingSummary] = []
    for class_instance in classes:
        summary = _serialize_training(class_instance, athlete_map)
        if summary:
            summaries.append(summary)

    return summaries


def get_training_summary(user: User, class_id: int) -> TrainingSummary | None:
    athlete_map = _collect_accessible_athletes(user)
    class_instance = _build_classes_queryset(user).filter(id=class_id).first()
    if not class_instance:
        return None
    return _serialize_training(class_instance, athlete_map)


def update_attendance_status(user: User, class_id: int, athlete_id: int, action: str) -> str:
    allowed_ids = set(
        get_athlete_queryset_for_user(user).values_list("id", flat=True)
    )
    if athlete_id not in allowed_ids:
        return "forbidden"

    status_map = {
        "confirm": ClassEnrollment.ASSISTANT_STATUS_CONFIRMED,
        "decline": ClassEnrollment.ASSISTANT_STATUS_DECLINED,
        "reset": ClassEnrollment.ASSISTANT_STATUS_UNKNOWN,
    }
    target_status = status_map.get(action)
    if not target_status:
        return "invalid"

    with transaction.atomic():
        enrollment = (
            ClassEnrollment.objects.select_for_update()
            .filter(class_instance_id=class_id, athlete_id=athlete_id)
            .first()
        )
        created = False
        if not enrollment:
            class_instance = (
                Class.objects.select_related("group")
                .filter(id=class_id)
                .first()
            )
            if not class_instance:
                return "not_found"
            if not class_instance.group.athletes.filter(id=athlete_id).exists():
                return "forbidden"
            enrollment = ClassEnrollment(
                class_instance=class_instance,
                athlete_id=athlete_id,
            )
            created = True

        enrollment.assistant_status = target_status
        if target_status == ClassEnrollment.ASSISTANT_STATUS_CONFIRMED:
            enrollment.confirmed = True
        elif target_status == ClassEnrollment.ASSISTANT_STATUS_DECLINED:
            enrollment.confirmed = False
        else:
            enrollment.confirmed = False
        if created:
            enrollment.save()
        else:
            enrollment.save(update_fields=["assistant_status", "confirmed"])

    return "ok"


def build_training_brief_lines(summary: TrainingSummary) -> list[str]:
    start = summary.start
    date_text = start.strftime("%d.%m %H:%M")
    lines = [f"{summary.emoji} {summary.training_type_display} — {date_text}"]
    lines.append(f"📍 Локация: {summary.location_display}")
    lines.append(f"👥 Группа: {summary.group_name}")
    if summary.equipment_display:
        lines.append(f"🎒 Экипировка: {summary.equipment_display}")
    coach_hint = COACH_STATUS_HINTS.get(summary.coach_status)
    if coach_hint:
        lines.append(coach_hint)
    elif summary.coach_status_display:
        lines.append(f"Статус тренера: {summary.coach_status_display}")
    return lines


def build_attendance_lines(summary: TrainingSummary) -> list[str]:
    if not summary.athletes:
        return ["Пока в этой тренировке нет спортсменов из вашей семьи."]

    lines = ["Участие вашей семьи:"]
    for attendee in summary.athletes:
        icon = ATTENDANCE_STATUS_ICONS.get(attendee.status_key, "•")
        lines.append(f"{icon} {attendee.short_name} — {attendee.status_text}")
    return lines


def build_training_details_text(summary: TrainingSummary) -> str:
    lines = build_training_brief_lines(summary)
    lines.append("")
    lines.extend(build_attendance_lines(summary))
    if summary.comment:
        lines.append("")
        lines.append(f"Комментарий: {summary.comment}")
    if summary.coach_comment:
        lines.append(f"Комментарий тренера: {summary.coach_comment}")
    return "\n".join(lines)
