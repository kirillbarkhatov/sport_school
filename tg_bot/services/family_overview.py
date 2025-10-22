from __future__ import annotations

import dataclasses

from django.urls import reverse

from school.models import Family, FamilyMember
from users.models import User


@dataclasses.dataclass(slots=True)
class FamilyMemberInfo:
    person_id: int | None
    title: str
    athlete_extra: str | None
    person_edit_path: str | None


@dataclasses.dataclass(slots=True)
class FamilyOverview:
    family_id: int
    family_name: str
    members: tuple[FamilyMemberInfo, ...]
    family_edit_path: str


def _format_member_title(member: FamilyMember, *, user_person_id: int | None) -> str:
    person = member.person
    if not person:
        return "Без данных"

    base = person.name or person.surname or "Без имени"
    is_user = user_person_id and person.id == user_person_id
    relation_display = member.get_relation_display()
    suffix_parts: list[str] = []
    if is_user:
        suffix_parts.append("вы")
    if relation_display:
        suffix_parts.append(relation_display.lower())
    if suffix_parts:
        return f"{base} ({', '.join(suffix_parts)})"
    return base


def _build_member_extra(member: FamilyMember) -> str | None:
    person = member.person
    if not person or not hasattr(person, "athlete"):
        return None

    athlete = person.athlete
    parts: list[str] = ["спортсмен"]
    if hasattr(athlete, "get_level_display"):
        level_display = athlete.get_level_display()
        if level_display:
            parts.append(f"уровень: {level_display}")
    if athlete.rank:
        parts.append(f"разряд: {athlete.rank}")
    return ", ".join(parts)


def get_family_overview(user: User) -> list[FamilyOverview]:
    family_ids = user.get_accessible_family_ids()
    if not family_ids:
        return []

    families = (
        Family.objects.filter(id__in=family_ids)
        .select_related("contact_person")
        .prefetch_related("members__person__athlete")
        .order_by("family_name", "id")
    )

    overviews: list[FamilyOverview] = []
    for family in families:
        members_payload: list[FamilyMemberInfo] = []
        seen_person_ids: set[int] = set()

        for member in family.members.all():
            person = member.person
            title = _format_member_title(member, user_person_id=user.person_id)
            athlete_extra = _build_member_extra(member)
            edit_path = reverse("members:members_update", args=[person.id]) if person else None
            if person and person.id in seen_person_ids:
                continue
            if person:
                seen_person_ids.add(person.id)
            members_payload.append(
                FamilyMemberInfo(
                    person_id=person.id if person else None,
                    title=title,
                    athlete_extra=athlete_extra,
                    person_edit_path=edit_path,
                )
            )

        contact_person = family.contact_person
        if contact_person and contact_person.id not in seen_person_ids:
            title = contact_person.name or contact_person.surname or "Контакт"
            if user.person_id and contact_person.id == user.person_id:
                title = f"{title} (вы)"
            members_payload.append(
                FamilyMemberInfo(
                    person_id=contact_person.id,
                    title=title,
                    athlete_extra=None,
                    person_edit_path=reverse("members:members_update", args=[contact_person.id]),
                )
            )

        overview = FamilyOverview(
            family_id=family.id,
            family_name=family.family_name or "Семья без названия",
            members=tuple(members_payload),
            family_edit_path=reverse("members:family_update", args=[family.id]),
        )
        overviews.append(overview)

    return overviews
