"""Внешние спортсмены (R2-5, docs/athletes-across-orgs.md).

Правило (консервативное): человек внешний, если в карточке указан клуб и это не клуб организации
(карточка «Канаев Ски Клаб» привязана к ksclub в school 0052), и при этом у него нет ни одной связи с
организацией: группы, семьи (участник или контактное лицо), профиля семьи или договора, учётной записи,
связи пользователя со спортсменом, роли тренера.

- Пустой или неизвестный клуб → НЕ внешний (неясно, чей человек); попадает в отчёт «без клуба».
- Появилась связь (семья, группа, договор, учётная запись, тренер) → пометка снимается автоматически.
- Внешних не удаляют: на них ссылаются заявки, участники соревнований и результаты.
"""

from __future__ import annotations

from django.db.models import Q
from django.db.models.signals import m2m_changed, post_save
from django.dispatch import receiver


def external_rule_q(organization_slug: str = "ksclub") -> Q:
    """Q для модели Person (подходит и для исторических моделей в миграции)."""
    has_foreign_club = Q(club__isnull=False) & ~Q(club__organization__slug=organization_slug)
    has_basis = (
        Q(athlete__groups_athletes__isnull=False)
        | Q(familymember__isnull=False)
        | Q(families__isnull=False)
        | Q(athlete__family_profiles__isnull=False)
        | Q(linked_users__isnull=False)
        | Q(athlete__user_links__isnull=False)
        | Q(coach__isnull=False)
    )
    return has_foreign_club & ~has_basis


def _slug() -> str:
    from .migration_utils import default_slug

    return default_slug()


def external_candidates(person_model=None):
    if person_model is None:
        from school.models import Person as person_model
    ids = person_model._base_manager.filter(external_rule_q(_slug())).values("pk")
    return person_model._base_manager.filter(pk__in=ids)


def mark_externals(person_model=None) -> int:
    """Помечает подходящих по правилу. Пометку не снимает (снимают сигналы при появлении связи). Идемпотентно."""
    return external_candidates(person_model).filter(is_external=False).update(is_external=True)


def unmark_person(person_id) -> None:
    if person_id:
        from school.models import Person

        Person._base_manager.filter(pk=person_id, is_external=True).update(is_external=False)


def report() -> dict[str, int]:
    """Агрегаты для orgs_externals_report и stage4_check (без персональных данных)."""
    from school.models import AthleteDocument, Person

    people = Person._base_manager.all()
    externals = people.filter(is_external=True)
    with_contacts = externals.filter(
        (Q(phone__isnull=False) & ~Q(phone=""))
        | (Q(email__isnull=False) & ~Q(email=""))
        | (Q(telegram__isnull=False) & ~Q(telegram=""))
        | Q(telegram_id__isnull=False)
    )
    with_documents = externals.filter(athlete__pk__in=AthleteDocument._base_manager.values("athlete_id"))
    return {
        "people": people.count(),
        "external": externals.count(),
        "external_athletes": externals.filter(athlete__isnull=False).count(),
        "without_club": people.filter(club__isnull=True).count(),
        "external_with_contacts": with_contacts.count(),
        "external_with_documents": with_documents.distinct().count(),
        "unmarked_candidates": external_candidates().filter(is_external=False).count(),
        "marked_with_basis": externals.exclude(pk__in=external_candidates().values("pk")).count(),
    }


def exclude_externals(queryset, person_path: str = ""):
    return queryset.exclude(**{f"{person_path}is_external": True})


EXTERNAL_FILTER_PARAM = "external"


def apply_external_filter(queryset, request, person_path: str = ""):
    """Рабочие списки: по умолчанию без внешних; ?external=only — только внешние; ?external=all — все."""
    mode = (request.GET.get(EXTERNAL_FILTER_PARAM) or "").lower()
    if mode == "only":
        return queryset.filter(**{f"{person_path}is_external": True})
    if mode == "all":
        return queryset
    return exclude_externals(queryset, person_path)


def mailing_recipients(users):
    """Получатели клубных рассылок: без внешних и без выпускников (членство «ушёл» в текущей организации).

    Организация вычисляется при выполнении запроса (OrgScope при включённой изоляции), поэтому помощник
    можно вызывать из асинхронного кода. При выключенной изоляции «текущая» — организация по умолчанию.
    """
    from .models import Membership
    from .scoping import OrgScope, scoping_enabled

    left = Membership.objects.filter(status=Membership.Status.LEFT)
    left = left.filter(OrgScope()) if scoping_enabled() else left.filter(organization__slug=_slug())
    return users.exclude(person__is_external=True).exclude(pk__in=left.values("user_id"))


# --- Появилась связь с организацией → человек больше не внешний ------------------------------------------


def _connect_signals():
    from school.models import Coach, FamilyAthleteProfile, FamilyMember, Group
    from users.models import User, UserAthleteLink

    @receiver(post_save, sender=FamilyMember, dispatch_uid="externals_family_member", weak=False)
    def _family_member(sender, instance, **_):
        unmark_person(instance.person_id)

    @receiver(post_save, sender=FamilyAthleteProfile, dispatch_uid="externals_family_profile", weak=False)
    def _family_profile(sender, instance, **_):
        unmark_person(getattr(instance.athlete, "person_id", None))

    @receiver(post_save, sender=Coach, dispatch_uid="externals_coach", weak=False)
    def _coach(sender, instance, **_):
        unmark_person(instance.person_id)

    @receiver(post_save, sender=User, dispatch_uid="externals_user", weak=False)
    def _user(sender, instance, **_):
        unmark_person(getattr(instance, "person_id", None))

    @receiver(post_save, sender=UserAthleteLink, dispatch_uid="externals_user_athlete", weak=False)
    def _user_athlete(sender, instance, **_):
        unmark_person(getattr(instance.athlete, "person_id", None))

    @receiver(m2m_changed, sender=Group.athletes.through, dispatch_uid="externals_group_athletes", weak=False)
    def _group_athletes(sender, instance, action, reverse, model, pk_set, **_):
        if action != "post_add":
            return
        from school.models import Athlete, Person

        athlete_ids = [instance.pk] if reverse else list(pk_set or [])
        person_ids = Athlete._base_manager.filter(pk__in=athlete_ids).values("person_id")
        Person._base_manager.filter(pk__in=person_ids, is_external=True).update(is_external=False)
