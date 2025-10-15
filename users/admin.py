from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.utils.translation import gettext_lazy as _

from .models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    fieldsets = (
        (None, {"fields": ("email", "password", "is_approved")}),
        (_("Персональные данные"), {"fields": ("first_name", "last_name", "phone", "tg_id", "tg_username", "family", "person")}),
        (_("Разрешения"), {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Важные даты"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "password1", "password2", "is_approved"),
        }),
    )
    list_display = ("email", "tg_id", "is_approved", "is_staff", "family")
    list_filter = ("is_approved", "is_staff", "family")
    search_fields = ("email", "first_name", "last_name", "tg_username")
    ordering = ("email",)
