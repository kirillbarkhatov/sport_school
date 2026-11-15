"""Единый слой прав (этап 4, R2-1). Все проверки доступа проходят через authorize / visible_to / has_role.

Источник ролей — флаг ORGS_ROLES_SOURCE:
- legacy      — как до этапа 4: is_approved, is_staff/is_superuser, группы Django. TELEGRAM_*_IDS веб-прав
                не дают (D12): их читает только бот, отдельной веткой в tg_bot/services/notifications.py;
- memberships — только активное членство в текущей организации и роли из него (D11): ни is_superuser,
                ни is_staff не обходят членство; глобальные роли — для консоли платформы, не для CRM.
                TELEGRAM_*_IDS не читаются нигде.

Переключение не расширяет доступ: действия, которые в legacy открыты только для is_staff/is_superuser
(«privileged»), в режиме членства требуют роль owner в текущей организации. Суперпользователь получает её
при переносе (import_legacy) в ksclub; в другой организации без членства прав у него нет.
"""

from __future__ import annotations

from django.conf import settings

from .models import Membership, OrgRole

LEGACY = "legacy"
MEMBERSHIPS = "memberships"

STAFF_ROLES = frozenset({OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MANAGER, OrgRole.COACH, OrgRole.SECRETARY})
MANAGEMENT_ROLES = frozenset({OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MANAGER})
PRIVILEGED = "privileged"
# Временный признак объекта пользователя в боте: менеджер, уже прошедший проверку роли в боте, видит
# все семьи и спортсменов (раньше бот ставил в памяти is_staff=True). В базу не сохраняется.
BOT_MANAGER_SCOPE = "bot_manager_scope"

# Действие → кому разрешено: множество ролей и/или PRIVILEGED (в legacy — is_staff/is_superuser,
# в memberships — роль owner в текущей организации). site.access обрабатывается отдельно.
ACTIONS: dict[str, frozenset[str]] = {
    "site.access": frozenset(),
    # Зона сотрудников: изменение соревнований, групп зачёта, занятий, групп, загрузка документов,
    # AI-анализ и привязка документов, внутренние выгрузки заявок (D04, D05).
    "staff.area": STAFF_ROLES | {PRIVILEGED},
    # Управление людьми и трансляциями: менеджеры и администраторы.
    "people.manage": MANAGEMENT_ROLES | {PRIVILEGED},
    "results.manage": MANAGEMENT_ROLES | {PRIVILEGED},
    # Расширенные фильтры спортсменов: все сотрудники.
    "athletes.extended_filters": STAFF_ROLES | {PRIVILEGED},
    # То, что раньше было только у is_staff/is_superuser.
    "org.full": frozenset({PRIVILEGED}),
}

_LEGACY_GROUP_ROLE = None


def roles_source() -> str:
    return getattr(settings, "ORGS_ROLES_SOURCE", LEGACY)


def _group_role_map() -> dict[str, str]:
    from .legacy import GROUP_TO_ROLE

    return GROUP_TO_ROLE


def _current_organization(organization=None):
    from .scoping import scope_organization

    return organization or scope_organization()


def active_membership(user, organization=None) -> Membership | None:
    organization = _current_organization(organization)
    if organization is None or not getattr(user, "is_authenticated", False):
        return None
    return Membership.objects.filter(user=user, organization=organization, status=Membership.Status.ACTIVE).first()


def roles_for(user, organization=None) -> set[str]:
    if not getattr(user, "is_authenticated", False):
        return set()
    if roles_source() == LEGACY:
        groups = _group_role_map()
        # Только группы Django: TELEGRAM_*_IDS в веб-проверках не участвовали и не участвуют (D12).
        return {groups[name] for name in user.groups.values_list("name", flat=True) if name in groups}
    membership = active_membership(user, organization)
    if membership is None:
        return set()
    return set(membership.role_assignments.values_list("role__code", flat=True))


def is_privileged(user, organization=None) -> bool:
    """Бывшая проверка `is_staff or is_superuser`. В memberships — роль owner в текущей организации (D11)."""
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, BOT_MANAGER_SCOPE, False):
        return True
    if roles_source() == LEGACY:
        return bool(user.is_staff or user.is_superuser)
    return OrgRole.OWNER in roles_for(user, organization)


def has_site_access(user, organization=None) -> bool:
    """Подтверждённый участник: legacy — is_approved, staff или superuser; memberships — только активное
    членство в текущей организации, без обхода для is_superuser (D11)."""
    if not getattr(user, "is_authenticated", False):
        return False
    if roles_source() == LEGACY:
        return bool(user.is_approved or user.is_staff or user.is_superuser)
    return active_membership(user, organization) is not None


def authorize(user, action: str, obj=None, organization=None) -> bool:
    if action not in ACTIONS:
        raise KeyError(f"Неизвестное действие: {action}")
    if action == "site.access":
        return has_site_access(user, organization)
    if not has_site_access(user, organization):
        return False
    allowed = ACTIONS[action]
    if PRIVILEGED in allowed and is_privileged(user, organization):
        return True
    return bool(roles_for(user, organization) & allowed)


def visible_to(user, action: str, queryset, organization=None):
    """Строки, видимые пользователю для действия. Фильтр по организации добавится в R2-4."""
    return queryset if authorize(user, action, organization=organization) else queryset.none()


def has_role(user, role_code: str, organization=None) -> bool:
    return role_code in roles_for(user, organization)
