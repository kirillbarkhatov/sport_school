"""Действия CRM над учётными записями (D16).

User — платформенная учётная запись, общая для всех организаций. При включённой изоляции CRM-роль меняет
только членство в своей организации; глобальные поля (пароль, статус, флаги) и удаление аккаунта — только
у платформенного администратора (глобальная роль global_admin). При выключенной изоляции — как в R1.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied


def is_platform_admin(user) -> bool:
    from .models import PlatformRole

    if not getattr(user, "is_authenticated", False):
        return False
    return PlatformRole.objects.filter(user=user, role=PlatformRole.Role.GLOBAL_ADMIN).exists()


def can_write_global_account(user) -> bool:
    from .permissions import is_privileged
    from .scoping import scoping_enabled

    if not scoping_enabled():
        return is_privileged(user)  # R1: is_staff/is_superuser (legacy) или owner (memberships)
    return is_platform_admin(user)


def set_user_approval(user, approved: bool, organization=None) -> list[str]:
    """Подтверждение или отказ пользователю в CRM. Возвращает поля User, которые вызывающему нужно сохранить.

    Изоляция выключена — user.is_approved (R1). Включена — статус членства в текущей организации
    (ACTIVE / SUSPENDED); is_approved для организации по умолчанию обновит синхронизация, на другие
    организации решение не влияет.
    """
    from .models import Membership
    from .scoping import scope_organization, scoping_enabled

    if not scoping_enabled():
        user.is_approved = approved
        return ["is_approved"]
    organization = organization or scope_organization()
    if organization is None:
        raise PermissionDenied("Нет организации для подтверждения.")
    membership, _ = Membership.objects.get_or_create(
        user=user, organization=organization, defaults={"status": Membership.Status.PENDING}
    )
    wanted = Membership.Status.ACTIVE if approved else Membership.Status.SUSPENDED
    if membership.status != wanted:
        membership.status = wanted
        membership.save(update_fields=["status"])
    user.is_approved = get_user_model().objects.filter(pk=user.pk).values_list("is_approved", flat=True).first()
    return []
