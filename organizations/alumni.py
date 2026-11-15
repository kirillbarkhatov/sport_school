"""Семьи-выпускники (R2-5, docs/athletes-across-orgs.md, «Вопрос 2»).

Консервативное правило: членство пользователя в организации переходит в «ушёл», только если
- его человек — участник или контактное лицо семьи со статусом «выпускник» (alumni), и
- нет ни одного основания оставаться активным: семьи со статусом «действующий» (через человека или
  связанного спортсмена), ролей в организации, групп Django, is_staff/is_superuser, глобальной роли,
  роли тренера;
- членство сейчас активно (ожидающие, приостановленные и ушедшие не трогаются).

Переход выполняет только команда `orgs_mark_alumni --apply` (по умолчанию — пробный прогон).
Доступ «ушедшего»: только чтение собственной истории, если включена настройка организации
`alumni_history_access` (по умолчанию да). В режиме legacy is_approved не снимается — неявного отзыва
прав нет; клубные рассылки выпускникам не приходят в обоих режимах (externals.mailing_recipients).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db.models import Q

ALUMNI = "alumni"
ACTIVE = "active"
HISTORY_SETTING = "alumni_history_access"


def history_access_enabled(organization) -> bool:
    return bool(organization is not None and (organization.settings or {}).get(HISTORY_SETTING, True))


def _in_family_with_status(status: str) -> Q:
    return (
        Q(person__familymember__family__status=status)
        | Q(person__families__status=status)
        | Q(athlete_links__athlete__family_profiles__family__status=status)
    )


def candidate_memberships(organization=None):
    from django.contrib.auth import get_user_model

    from .context import default_organization
    from .models import Membership, PlatformRole, RoleAssignment

    organization = organization or default_organization()
    User = get_user_model()
    alumni_users = User.objects.filter(_in_family_with_status(ALUMNI)).values("pk")
    keep = User.objects.filter(
        _in_family_with_status(ACTIVE)
        | Q(groups__isnull=False)
        | Q(is_staff=True)
        | Q(is_superuser=True)
        | Q(person__coach__isnull=False)
    ).values("pk")
    with_roles = RoleAssignment.objects.filter(membership__organization=organization).values("membership__user_id")
    platform = PlatformRole.objects.values("user_id")
    return (
        Membership.objects.filter(organization=organization, status=Membership.Status.ACTIVE)
        .filter(user_id__in=alumni_users)
        .exclude(user_id__in=keep)
        .exclude(user_id__in=with_roles)
        .exclude(user_id__in=platform)
    )


@dataclass
class AlumniReport:
    alumni_families: int = 0
    candidates: int = 0
    marked: int = 0
    kept_with_basis: int = 0
    membership_ids: list[int] = field(default_factory=list)


def mark_alumni(*, apply: bool, organization=None, actor=None) -> AlumniReport:
    from django.contrib.auth import get_user_model
    from django.db import transaction

    from school.models import Family

    from .context import default_organization
    from .models import AuditLog, Membership

    organization = organization or default_organization()
    report = AlumniReport(alumni_families=Family.all_objects.filter(status=ALUMNI).count())
    candidates = list(candidate_memberships(organization).select_related("user"))
    report.candidates = len(candidates)
    linked = get_user_model().objects.filter(_in_family_with_status(ALUMNI)).distinct().count()
    report.kept_with_basis = max(linked - report.candidates, 0)
    if not apply:
        return report
    with transaction.atomic():
        for membership in candidates:
            membership.status = Membership.Status.LEFT
            membership.save(update_fields=["status"])
            report.membership_ids.append(membership.pk)
        report.marked = len(candidates)
        AuditLog.objects.create(
            organization=organization,
            actor=actor,
            action="alumni_marked",
            payload={"marked": report.marked, "kept_with_basis": report.kept_with_basis},
        )
    return report


def history_only_membership(user, organization):
    """Членство «ушёл» с доступом к истории только на чтение (или None)."""
    from .models import Membership

    if organization is None or not history_access_enabled(organization):
        return None
    if not getattr(user, "is_authenticated", False):
        return None
    return Membership.objects.filter(user=user, organization=organization, status=Membership.Status.LEFT).first()


def is_history_only(user, organization=None) -> bool:
    """Пользователь без активного членства, но с «ушёл» и доступом к истории — только чтение."""
    from .models import Membership
    from .scoping import scope_organization

    organization = organization or scope_organization()
    if organization is None or not getattr(user, "is_authenticated", False):
        return False
    statuses = set(Membership.objects.filter(user=user, organization=organization).values_list("status", flat=True))
    return (
        Membership.Status.LEFT in statuses
        and Membership.Status.ACTIVE not in statuses
        and (history_access_enabled(organization))
    )


class HistoryReadOnlyMiddleware:
    """Выпускник (членство «ушёл») видит свою историю, но ничего не меняет: изменяющие запросы → 403.
    Вход и выход (пространство имён users) не ограничиваются."""

    SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method not in self.SAFE_METHODS and is_history_only(getattr(request, "user", None)):
            from django.http import HttpResponseForbidden
            from django.urls import Resolver404, resolve

            try:
                namespace = resolve(request.path_info).namespace
            except Resolver404:
                namespace = ""
            if namespace != "users":
                return HttpResponseForbidden("Доступ только для просмотра истории.")
        return self.get_response(request)
