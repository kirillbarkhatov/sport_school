from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import User, UserPersonLink


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = (
        (None, {"fields": ("email", "password", "is_approved", "bot_access")}),
        (_("Персональные данные"), {"fields": ("first_name", "last_name", "phone", "tg_id", "tg_username", "person")}),
        (_("Разрешения"), {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Важные даты"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "password1", "password2", "is_approved", "bot_access"),
        }),
    )
    list_display = ("email", "tg_id", "is_approved", "bot_access", "is_staff", "person")
    list_filter = ("is_approved", "bot_access", "is_staff", "person")
    search_fields = ("email", "first_name", "last_name", "tg_username")
    ordering = ("email",)


@admin.register(UserPersonLink)
class UserPersonLinkAdmin(admin.ModelAdmin):
    list_display = ("user", "suggested_person", "status", "decided_by", "updated_at")
    list_filter = ("status",)
    search_fields = (
        "user__email",
        "user__first_name",
        "user__last_name",
        "suggested_person__surname",
        "suggested_person__name",
    )
    readonly_fields = ("matched_reasons", "user_comment", "user_comment_updated_at")

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            readonly.extend(["status", "decided_by", "decided_at", "decision_note"])
        return readonly
