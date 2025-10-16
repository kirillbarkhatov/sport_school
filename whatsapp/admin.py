from django.contrib import admin

from .models import WhatsAppChat, WhatsAppChatMember


@admin.register(WhatsAppChat)
class WhatsAppChatAdmin(admin.ModelAdmin):
    list_display = ("name", "last_synced_at", "created_at", "updated_at")
    search_fields = ("name",)
    readonly_fields = ("created_at", "updated_at")
    ordering = ("-last_synced_at", "-updated_at")


@admin.register(WhatsAppChatMember)
class WhatsAppChatMemberAdmin(admin.ModelAdmin):
    list_display = (
        "member_id",
        "chat",
        "push_name",
        "phone",
        "is_admin",
        "is_super_admin",
        "is_active",
        "last_seen_at",
    )
    list_filter = ("chat", "is_admin", "is_super_admin", "is_active")
    search_fields = ("member_id", "push_name", "phone")
    readonly_fields = ("first_seen_at", "last_seen_at", "left_at")
    autocomplete_fields = ("chat",)
