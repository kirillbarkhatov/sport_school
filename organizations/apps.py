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
