"""Связь новой модели (членство, роли) со старой (is_approved, группы Django, is_staff, TELEGRAM_*_IDS).

- import_legacy(): идемпотентный перенос старых полей в членство и роли.
- Двойная запись (ORGS_LEGACY_SYNC): изменения с обеих сторон пересчитываются, без рекурсии.
  Источник правды — членство и роли; старые поля поддерживаются, пока их читает старый код.
- is_staff и is_superuser двойной записью НЕ меняются: это доступ к Django-админке, его нельзя
  выдавать через роли организации (переносится только в одну сторону: флаг → роль).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.utils import timezone

from users.constants import ADMIN_GROUP_NAME, COACH_GROUP_NAME, MANAGER_GROUP_NAME

from .context import default_organization
from .models import AuditLog, Membership, OrgRole, PlatformRole, RoleAssignment, Source

GROUP_TO_ROLE = {ADMIN_GROUP_NAME: OrgRole.ADMIN, MANAGER_GROUP_NAME: OrgRole.MANAGER, COACH_GROUP_NAME: OrgRole.COACH}
ROLE_TO_GROUP = {role: group for group, role in GROUP_TO_ROLE.items()}
ENV_TO_ROLE = {
    "TELEGRAM_ADMIN_IDS": OrgRole.ADMIN,
    "TELEGRAM_MANAGER_IDS": OrgRole.MANAGER,
    "TELEGRAM_COACH_IDS": OrgRole.COACH,
}
AUTOMATIC_SOURCES = (Source.LEGACY_IMPORT, Source.SYNC)

_syncing: ContextVar[bool] = ContextVar("orgs_legacy_syncing", default=False)


@contextmanager
def _sync_guard():
    token = _syncing.set(True)
    try:
        yield
    finally:
        _syncing.reset(token)


def sync_enabled() -> bool:
    return bool(getattr(settings, "ORGS_LEGACY_SYNC", True)) and not _syncing.get()


def _role(code: str) -> OrgRole:
    return OrgRole.objects.get(organization__isnull=True, code=code)


def _membership_status_for(user) -> str:
    approved = user.is_approved or user.is_staff or user.is_superuser
    return Membership.Status.ACTIVE if approved else Membership.Status.PENDING


@dataclass
class ImportReport:
    memberships_created: int = 0
    roles_created: int = 0
    roles_revoked: int = 0
    platform_roles_created: int = 0
    users: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_role: dict[str, int] = field(default_factory=dict)


def _env_tg_ids(name: str) -> set[int]:
    values = getattr(settings, name, []) or []
    result = set()
    for value in values:
        try:
            result.add(int(str(value).strip()))
        except ValueError:
            continue
    return result


AUTO_MANAGED_ROLES = (OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MANAGER, OrgRole.COACH)


def roles_source_is_legacy() -> bool:
    return getattr(settings, "ORGS_ROLES_SOURCE", "legacy") == "legacy"


def legacy_basis(user, *, include_env: bool) -> tuple[set[str], bool]:
    """Какие роли и нужна ли глобальная роль по старым полям: группы, is_superuser, is_staff, TELEGRAM_*_IDS."""
    wanted = {GROUP_TO_ROLE[name] for name in user.groups.values_list("name", flat=True) if name in GROUP_TO_ROLE}
    if include_env and user.tg_id:
        wanted |= {role for name, role in ENV_TO_ROLE.items() if user.tg_id in _env_tg_ids(name)}
    if user.is_superuser:
        wanted.add(OrgRole.OWNER)
    elif user.is_staff:
        wanted.add(OrgRole.ADMIN)
    return wanted, bool(user.is_superuser)


def reconcile_user_roles(
    user, membership: Membership, *, source: str, include_env: bool, revoke: bool
) -> tuple[int, int, int]:
    """Привести автоматические роли к основаниям. Ручные назначения (source=manual) не трогаются никогда.

    Отзываются только автоматические назначения (legacy_import/sync) ролей owner/admin/manager/coach, у которых
    больше нет ни одного основания (группа, is_superuser/is_staff, Telegram-список до переключения).
    Возвращает (создано назначений, создано глобальных ролей, отозвано).
    """
    wanted, wants_global_admin = legacy_basis(user, include_env=include_env)
    created = platform_created = revoked = 0
    for code in wanted:
        _, made = RoleAssignment.objects.get_or_create(
            membership=membership, role=_role(code), defaults={"source": source}
        )
        created += int(made)
    if wants_global_admin:
        _, made = PlatformRole.objects.get_or_create(
            user=user, role=PlatformRole.Role.GLOBAL_ADMIN, defaults={"source": source}
        )
        platform_created += int(made)
    if revoke:
        stale = RoleAssignment.objects.filter(
            membership=membership, role__code__in=set(AUTO_MANAGED_ROLES) - wanted, source__in=AUTOMATIC_SOURCES
        )
        revoked += stale.count()
        stale.delete()
        if not wants_global_admin:
            stale_platform = PlatformRole.objects.filter(
                user=user, role=PlatformRole.Role.GLOBAL_ADMIN, source__in=AUTOMATIC_SOURCES
            )
            revoked += stale_platform.count()
            stale_platform.delete()
    return created, platform_created, revoked


def import_legacy() -> ImportReport:
    """Перенос старых полей. Идемпотентен; ручные назначения и явные статусы не меняет.

    До переключения (ORGS_ROLES_SOURCE=legacy) роли сверяются с основаниями, включая TELEGRAM_*_IDS, и
    потерявшие основание автоматические роли отзываются (D07). После переключения источник правды — членство:
    импорт только заводит членства новым пользователям, роли не трогает и Telegram-списки не читает.
    """
    organization = default_organization()
    if organization is None:
        raise RuntimeError("Нет организации по умолчанию (ORGS_DEFAULT_ORGANIZATION_SLUG)")
    report = ImportReport()
    before_switch = roles_source_is_legacy()
    User = get_user_model()
    with transaction.atomic(), _sync_guard():
        for user in User.objects.prefetch_related("groups"):
            report.users += 1
            membership, created = Membership.objects.get_or_create(
                user=user,
                organization=organization,
                defaults={"status": _membership_status_for(user), "source": Source.LEGACY_IMPORT},
            )
            if created:
                report.memberships_created += 1
                if membership.status == Membership.Status.ACTIVE:
                    membership.approved_at = timezone.now()
                    membership.save(update_fields=["approved_at"])
            if before_switch:
                made, platform_made, revoked = reconcile_user_roles(
                    user, membership, source=Source.LEGACY_IMPORT, include_env=True, revoke=True
                )
                report.roles_created += made
                report.platform_roles_created += platform_made
                report.roles_revoked += revoked

        for membership in Membership.objects.filter(organization=organization):
            report.by_status[membership.status] = report.by_status.get(membership.status, 0) + 1
        for assignment in RoleAssignment.objects.filter(membership__organization=organization).select_related("role"):
            report.by_role[assignment.role.code] = report.by_role.get(assignment.role.code, 0) + 1
        AuditLog.objects.create(
            organization=organization,
            action="legacy_import",
            payload={
                "memberships_created": report.memberships_created,
                "roles_created": report.roles_created,
                "roles_revoked": report.roles_revoked,
                "roles_reconciled": before_switch,
            },
        )
    return report


# --- Двойная запись: новая модель → старые поля ------------------------------------------------------


def push_membership_to_legacy(membership: Membership) -> None:
    """Статус членства в организации по умолчанию → user.is_approved."""
    if not sync_enabled():
        return
    organization = default_organization()
    if organization is None or membership.organization_id != organization.pk:
        return
    user = membership.user
    if membership.status == Membership.Status.LEFT:
        # выпускник: с доступом к истории is_approved не снимается (без неявного отзыва), без него — снимается
        from .alumni import history_access_enabled

        if history_access_enabled(organization):
            return
    approved = membership.status == Membership.Status.ACTIVE
    if user.is_approved != approved:
        with _sync_guard():
            type(user).objects.filter(pk=user.pk).update(is_approved=approved)
            user.is_approved = approved


def push_roles_to_legacy(membership: Membership) -> None:
    """Роли admin/manager/coach в организации по умолчанию → группы Django."""
    if not sync_enabled():
        return
    organization = default_organization()
    if organization is None or membership.organization_id != organization.pk:
        return
    codes = set(membership.role_assignments.values_list("role__code", flat=True))
    user = membership.user
    with _sync_guard():
        for role_code, group_name in ROLE_TO_GROUP.items():
            group, _ = Group.objects.get_or_create(name=group_name)
            has_group = user.groups.filter(pk=group.pk).exists()
            if role_code in codes and not has_group:
                user.groups.add(group)
            elif role_code not in codes and has_group:
                user.groups.remove(group)


# --- Двойная запись: старые поля → новая модель (только до переключения источника) --------------------


def _ensure_membership(user, organization) -> tuple[Membership, bool]:
    return Membership.objects.get_or_create(
        user=user, organization=organization, defaults={"status": _membership_status_for(user), "source": Source.SYNC}
    )


def pull_user_to_membership(user) -> None:
    """Изменили is_approved / is_staff / is_superuser в старом коде или админке → членство и роли."""
    if not sync_enabled():
        return
    organization = default_organization()
    if organization is None:
        return
    with _sync_guard():
        membership, created = _ensure_membership(user, organization)
        if not created and membership.status in (Membership.Status.ACTIVE, Membership.Status.PENDING):
            wanted = _membership_status_for(user)
            if membership.status != wanted:
                membership.status = wanted
                membership.approved_at = timezone.now() if wanted == Membership.Status.ACTIVE else None
                membership.save(update_fields=["status", "approved_at"])
        if roles_source_is_legacy():
            reconcile_user_roles(user, membership, source=Source.SYNC, include_env=True, revoke=True)


def pull_groups_to_roles(user) -> None:
    """Изменили группы пользователя в старом коде или админке → роли. Ручные назначения не удаляются."""
    if not sync_enabled() or not roles_source_is_legacy():
        return  # после переключения источник правды — членство; группы только отражают роли
    organization = default_organization()
    if organization is None:
        return
    with _sync_guard():
        membership, _ = _ensure_membership(user, organization)
        reconcile_user_roles(user, membership, source=Source.SYNC, include_env=True, revoke=True)


# --- Сверка ------------------------------------------------------------------------------------------


@dataclass
class Discrepancies:
    users_without_membership: int = 0
    approval_mismatch: int = 0
    group_role_mismatch: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.users_without_membership + self.approval_mismatch + sum(self.group_role_mismatch.values())


def find_discrepancies() -> Discrepancies:
    organization = default_organization()
    result = Discrepancies()
    User = get_user_model()
    memberships = {m.user_id: m for m in Membership.objects.filter(organization=organization)}
    roles: dict[int, set[str]] = {}
    for user_id, code in RoleAssignment.objects.filter(membership__organization=organization).values_list(
        "membership__user_id", "role__code"
    ):
        roles.setdefault(user_id, set()).add(code)
    for user in User.objects.prefetch_related("groups"):
        membership = memberships.get(user.pk)
        if membership is None:
            result.users_without_membership += 1
            continue
        approved = user.is_approved or user.is_staff or user.is_superuser
        if membership.status in (Membership.Status.ACTIVE, Membership.Status.PENDING) and approved != (
            membership.status == Membership.Status.ACTIVE
        ):
            result.approval_mismatch += 1
        groups = {g.name for g in user.groups.all()}
        user_roles = roles.get(user.pk, set())
        for group_name, code in GROUP_TO_ROLE.items():
            if (group_name in groups) and code not in user_roles:
                result.group_role_mismatch[code] = result.group_role_mismatch.get(code, 0) + 1
    return result
