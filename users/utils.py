from typing import Iterable

from django.db.models import QuerySet

from school.models import FamilyMember, Person, Athlete, Group, Class


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
