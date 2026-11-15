from rest_framework.permissions import BasePermission


class IsApprovedUser(BasePermission):
    """Вошёл и подтверждён администратором (или сотрудник). Базовое право для API по умолчанию."""

    message = "Учётная запись ещё не подтверждена администратором."

    def has_permission(self, request, view) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return bool(user.is_staff or user.is_superuser or getattr(user, "is_approved", False))
