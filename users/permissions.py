from rest_framework.permissions import BasePermission

from organizations.permissions import has_site_access, is_privileged


class IsApprovedUser(BasePermission):
    """Вошёл и подтверждён администратором (или сотрудник). Базовое право для API по умолчанию."""

    message = "Учётная запись ещё не подтверждена администратором."

    def has_permission(self, request, view) -> bool:
        return has_site_access(request.user)


class IsPrivilegedUser(BasePermission):
    """Бывший IsAdminUser (is_staff): через единый слой прав, в memberships — owner текущей организации."""

    def has_permission(self, request, view) -> bool:
        return is_privileged(request.user)
