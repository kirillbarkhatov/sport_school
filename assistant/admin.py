from __future__ import annotations

from django.contrib import admin

from .models import AssistantSyncLog, AssistantUnmatchedParticipant, ServiceAccount


@admin.register(ServiceAccount)
class ServiceAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "key", "is_active", "created_at")
    readonly_fields = ("created_at", "updated_at")
    search_fields = ("name", "slug", "key")
    list_filter = ("is_active",)
    fieldsets = (
        (None, {"fields": ("name", "slug", "is_active")}),
        (
            "JWT параметры",
            {
                "fields": ("key", "secret", "issuer", "audience", "lifetime_seconds"),
                "description": "Секрет хранится в открытом виде. После изменения сохраните и заново выдайте токен интеграции.",
            },
        ),
        ("Системные поля", {"fields": ("created_at", "updated_at")}),
    )


@admin.register(AssistantSyncLog)
class AssistantSyncLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "direction", "event_type", "service_account", "status_code")
    list_filter = ("direction", "event_type", "status_code")
    search_fields = ("event_type", "request_url")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("service_account",)


@admin.register(AssistantUnmatchedParticipant)
class AssistantUnmatchedParticipantAdmin(admin.ModelAdmin):
    list_display = ("full_name", "phone", "training", "service_account", "received_at")
    list_filter = ("service_account",)
    search_fields = ("full_name", "phone", "comment")
    readonly_fields = ("received_at",)
    autocomplete_fields = ("training", "service_account")
