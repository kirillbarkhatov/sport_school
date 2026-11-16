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


def _in_family_with_status(status: str | None, organization) -> Q:
    """Пользователь связан с семьёй (статус status или любой при None) ТОЛЬКО этой организации (D18).
    Условия на одну связь в одном Q — один JOIN, поэтому статус и организация относятся к одной семье."""

    def family(prefix):
        conditions = {f"{prefix}__organization": organization}
        if status is not None:
            conditions[f"{prefix}__status"] = status
        return Q(**conditions)

    return (
        family("person__familymember__family")
        | family("person__families")
        | family("athlete_links__athlete__family_profiles__family")
    )


def candidate_memberships(organization=None):
    from django.contrib.auth import get_user_model

    from .context import default_organization
    from .models import Membership, PlatformRole, RoleAssignment

    organization = organization or default_organization()
    if organization is None:
        return Membership.objects.none()
    User = get_user_model()
    alumni_users = User.objects.filter(_in_family_with_status(ALUMNI, organization)).values("pk")
    basis = (
        _in_family_with_status(ACTIVE, organization)
        | Q(person__coach__organization=organization)
        | Q(is_staff=True)  # глобальные флаги — консервативно сохраняем активность
        | Q(is_superuser=True)
    )
    if organization.slug == _default_slug():
        basis |= Q(groups__isnull=False)  # группы Django — роли старой схемы организации по умолчанию
    keep = User.objects.filter(basis).values("pk")
    # роли только ЭТОЙ организации: активная роль в другом клубе не мешает «ушёл» здесь (D18)
    with_roles = RoleAssignment.objects.filter(membership__organization=organization).values("membership__user_id")
    platform = PlatformRole.objects.values("user_id")
    return (
        Membership.objects.filter(organization=organization, status=Membership.Status.ACTIVE)
        .filter(user_id__in=alumni_users)
        .exclude(user_id__in=keep)
        .exclude(user_id__in=with_roles)
        .exclude(user_id__in=platform)
        .exclude(approved_by__isnull=False)  # явное решение администратора о членстве сохраняется
        .exclude(pk__in=_reverted_by_admin(organization))
    )


def _default_slug() -> str:
    from .migration_utils import default_slug

    return default_slug()


def _reverted_by_admin(organization) -> set[int]:
    """Членства, которые уже переводились в «ушёл», но сейчас активны, — их вернул администратор."""
    from .models import AuditLog

    ids: set[int] = set()
    for payload in AuditLog.objects.filter(organization=organization, action="alumni_marked").values_list(
        "payload", flat=True
    ):
        ids.update((payload or {}).get("membership_ids", []))
    return ids


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
    report = AlumniReport(alumni_families=Family.all_objects.filter(status=ALUMNI, organization=organization).count())
    candidates = list(candidate_memberships(organization).select_related("user"))
    report.candidates = len(candidates)
    linked = get_user_model().objects.filter(_in_family_with_status(ALUMNI, organization)).distinct().count()
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
            payload={
                "marked": report.marked,
                "kept_with_basis": report.kept_with_basis,
                "membership_ids": report.membership_ids,
            },
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


# Публичные маршруты, открытые выпускнику (и любому посетителю): здоровье, публичные трансляции.
HISTORY_PUBLIC_URL_NAMES = frozenset(
    {
        "healthz",
        "public_live",
        "public_fragment",
        "online-results-live-public",
        "online-results-live-public-state",
        "online-results-live-public-document",
    }
)


class HistoryReadOnlyMiddleware:
    """Выпускник (членство «ушёл», право history.read) — D17:
    - изменяющие запросы → 403 (кроме входа и выхода, пространство имён users);
    - GET: публичные маршруты, вход и выход, а также страницы с ApprovedUserRequiredMixin — они сами
      пускают только history_read_allowed-страницы со своими семьями; остальное → к своим семьям.
    """

    SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False) or not is_history_only(user):
            return self.get_response(request)
        from django.http import HttpResponseForbidden
        from django.shortcuts import redirect
        from django.urls import Resolver404, resolve

        try:
            match = resolve(request.path_info)
        except Resolver404:
            return self.get_response(request)
        if match.namespace == "users":
            return self.get_response(request)
        if request.method not in self.SAFE_METHODS:
            return HttpResponseForbidden("Доступ только для просмотра истории.")
        if match.url_name in HISTORY_PUBLIC_URL_NAMES:
            return self.get_response(request)
        from users.mixins import ApprovedUserRequiredMixin

        view_class = getattr(match.func, "view_class", None)
        if view_class is not None and issubclass(view_class, ApprovedUserRequiredMixin):
            return self.get_response(request)  # решает сама страница (history_read_allowed)
        return redirect("members:family_list")


def can_read_history(user, organization=None) -> bool:
    """Право history.read: выпускник видит только свои семейные записи (D17). Это не доступ к сайту."""
    return is_history_only(user, organization)


def history_family_ids(user, organization=None) -> list[int]:
    """Семьи этой организации, с которыми связан пользователь: участник, контактное лицо, родитель спортсмена."""
    from school.models import Family

    from .scoping import scope_organization

    organization = organization or scope_organization()
    if organization is None or not getattr(user, "is_authenticated", False):
        return []
    q = Q(athlete_profiles__athlete__user_links__user=user)
    if user.person_id:
        q |= Q(members__person_id=user.person_id) | Q(contact_person_id=user.person_id)
    return list(Family.all_objects.filter(organization=organization).filter(q).values_list("pk", flat=True).distinct())
