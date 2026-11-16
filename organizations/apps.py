from django.apps import AppConfig


class OrganizationsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "organizations"
    verbose_name = "Организации"

    def ready(self):
        from . import signals  # noqa: F401
        from .context import connect_celery_signals

        connect_celery_signals()

        from .externals import _connect_signals

        _connect_signals()
        restrict_admin_site()


def admin_has_permission(request) -> bool:
    """R3-2: Django-админка только для глобального администратора (PlatformRole global_admin) с is_staff."""
    from .accounts import is_platform_admin

    user = request.user
    return bool(user.is_active and user.is_staff and is_platform_admin(user))


def restrict_admin_site() -> None:
    from django.contrib import admin

    admin.site.has_permission = admin_has_permission
