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


def import_legacy() -> ImportReport:
    """Перенос старых полей. Повторный запуск не дублирует данные и не трогает ручные назначения/статусы."""
    organization = default_organization()
    if organization is None:
        raise RuntimeError("Нет организации по умолчанию (ORGS_DEFAULT_ORGANIZATION_SLUG)")
    report = ImportReport()
    env_roles = {role: _env_tg_ids(name) for name, role in ENV_TO_ROLE.items()}
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

            wanted = {GROUP_TO_ROLE[g.name] for g in user.groups.all() if g.name in GROUP_TO_ROLE}
            for role_code, tg_ids in env_roles.items():
                if user.tg_id and user.tg_id in tg_ids:
                    wanted.add(role_code)
            if user.is_superuser:
                wanted.add(OrgRole.OWNER)
                _, made = PlatformRole.objects.get_or_create(
                    user=user, role=PlatformRole.Role.GLOBAL_ADMIN, defaults={"source": Source.LEGACY_IMPORT}
                )
                report.platform_roles_created += int(made)
            elif user.is_staff:
                wanted.add(OrgRole.ADMIN)
            for code in wanted:
                _, made = RoleAssignment.objects.get_or_create(
                    membership=membership, role=_role(code), defaults={"source": Source.LEGACY_IMPORT}
                )
                report.roles_created += int(made)

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
                "platform_roles_created": report.platform_roles_created,
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


# --- Двойная запись: старые поля → новая модель ------------------------------------------------------


def pull_user_to_membership(user) -> None:
    """user.is_approved изменили в старом коде/админке → статус членства."""
    if not sync_enabled():
        return
    organization = default_organization()
    if organization is None:
        return
    with _sync_guard():
        membership, created = Membership.objects.get_or_create(
            user=user,
            organization=organization,
            defaults={"status": _membership_status_for(user), "source": Source.SYNC},
        )
        if created:
            return
        if membership.status in (Membership.Status.ACTIVE, Membership.Status.PENDING):
            wanted = _membership_status_for(user)
            if membership.status != wanted:
                membership.status = wanted
                membership.approved_at = timezone.now() if wanted == Membership.Status.ACTIVE else None
                membership.save(update_fields=["status", "approved_at"])


def pull_groups_to_roles(user) -> None:
    """Группы пользователя изменили в старом коде/админке → роли. Ручные назначения не удаляются."""
    if not sync_enabled():
        return
    organization = default_organization()
    if organization is None:
        return
    with _sync_guard():
        membership, _ = Membership.objects.get_or_create(
            user=user,
            organization=organization,
            defaults={"status": _membership_status_for(user), "source": Source.SYNC},
        )
        group_roles = {
            GROUP_TO_ROLE[name] for name in user.groups.values_list("name", flat=True) if name in GROUP_TO_ROLE
        }
        for code in group_roles:
            RoleAssignment.objects.get_or_create(
                membership=membership, role=_role(code), defaults={"source": Source.SYNC}
            )
        stale = RoleAssignment.objects.filter(
            membership=membership, role__code__in=set(ROLE_TO_GROUP) - group_roles, source__in=AUTOMATIC_SOURCES
        )
        # роль, пришедшая из TELEGRAM_*_IDS или is_staff, удаляется только вместе с группой, если она из синхронизации
        stale.filter(source=Source.SYNC).delete()
        stale.filter(source=Source.LEGACY_IMPORT, role__code__in=_group_only_roles(user)).delete()


def _group_only_roles(user) -> set[str]:
    """Роли, которые у пользователя держались только на группе (не на is_staff/TELEGRAM_*_IDS)."""
    kept = set()
    if user.is_staff and not user.is_superuser:
        kept.add(OrgRole.ADMIN)
    for name, role in ENV_TO_ROLE.items():
        if user.tg_id and user.tg_id in _env_tg_ids(name):
            kept.add(role)
    return set(ROLE_TO_GROUP) - kept


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
