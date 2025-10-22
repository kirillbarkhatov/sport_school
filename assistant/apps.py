from django.apps import AppConfig


class AssistantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "assistant"
    verbose_name = "AI Assistant Integration"

    def ready(self):
        # Import signal handlers
        from . import signals  # noqa: F401
