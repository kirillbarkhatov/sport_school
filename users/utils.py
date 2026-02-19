from typing import Iterable

from django.db import models
from django.db.models import QuerySet

from school.models import FamilyMember, Person, Athlete, Group, Class, Coach
from users.models import UserAthleteLink


def get_person_queryset_for_user(user) -> QuerySet:
    if user.is_staff or user.is_superuser:
        return Person.objects.all()

    family_ids = user.get_accessible_family_ids()
    person_ids = list(
        FamilyMember.objects.filter(
            family_id__in=family_ids
        ).values_list("person_id", flat=True)
    )
    if user.person_id:
        person_ids.append(user.person_id)

    if not person_ids:
        return Person.objects.none()

    unique_ids = list(set(person_ids))
    return Person.objects.filter(id__in=unique_ids)


def get_athlete_queryset_for_user(user) -> QuerySet:
    if user.is_staff or user.is_superuser:
        return Athlete.objects.all()

    people = get_person_queryset_for_user(user).values_list("id", flat=True)
    return Athlete.objects.filter(person_id__in=people)


def get_group_queryset_for_user(user) -> QuerySet:
    if user.is_staff or user.is_superuser:
        return Group.objects.all()

    athlete_ids = get_athlete_queryset_for_user(user).values_list("id", flat=True)
    return Group.objects.filter(athletes__id__in=athlete_ids).distinct()


def get_class_queryset_for_user(user) -> QuerySet:
    if user.is_staff or user.is_superuser:
        return Class.objects.all()

    athlete_ids = get_athlete_queryset_for_user(user).values_list("id", flat=True)
    return Class.objects.filter(
        enrollments__athlete_id__in=athlete_ids
    ).distinct()


def get_user_linked_athlete_queryset(user) -> QuerySet:
    return Athlete.objects.filter(user_links__user=user)


def get_coach_club_ids_for_user(user) -> set[int]:
    if not user.person_id:
        return set()
    coach = (
        Coach.objects.select_related("person__club")
        .filter(person_id=user.person_id)
        .first()
    )
    if not coach or not coach.person or not coach.person.club_id:
        return set()
    return {coach.person.club_id}


def ensure_user_athlete_links(user, athletes: QuerySet, *, source: str = "family") -> None:
    athlete_ids = list(athletes.values_list("id", flat=True))
    if not athlete_ids:
        return
    existing_ids = set(
        UserAthleteLink.objects.filter(
            user=user,
            athlete_id__in=athlete_ids,
        ).values_list("athlete_id", flat=True)
    )
    to_create = [
        UserAthleteLink(user=user, athlete_id=athlete_id, source=source)
        for athlete_id in athlete_ids
        if athlete_id not in existing_ids
    ]
    if to_create:
        UserAthleteLink.objects.bulk_create(to_create, ignore_conflicts=True)


def get_available_athlete_queryset_for_user(user, *, ensure_family_links: bool = False) -> QuerySet:
    if user.is_staff or user.is_superuser:
        return Athlete.objects.all()

    coach_club_ids = get_coach_club_ids_for_user(user)
    if coach_club_ids:
        return Athlete.objects.filter(person__club_id__in=coach_club_ids)

    linked_qs = get_user_linked_athlete_queryset(user)
    if user.is_approved:
        family_qs = get_athlete_queryset_for_user(user)
        if ensure_family_links:
            ensure_user_athlete_links(user, family_qs, source="family")
        return Athlete.objects.filter(
            models.Q(pk__in=family_qs.values_list("id", flat=True))
            | models.Q(pk__in=linked_qs.values_list("id", flat=True))
        ).distinct()

    return linked_qs
