from django.contrib import admin

from bot.models import TelegramChat, TelegramParticipant


@admin.register(TelegramChat)
class TelegramChatAdmin(admin.ModelAdmin):
    list_display = ("chat_id", "type", "title", "username", "last_seen")
    search_fields = ("chat_id", "title", "username")
    list_filter = ("type",)
    readonly_fields = ("first_seen", "last_seen")
    ordering = ("-last_seen",)


@admin.register(TelegramParticipant)
class TelegramParticipantAdmin(admin.ModelAdmin):
    list_display = (
        "user_id",
        "chat",
        "username",
        "first_name",
        "last_seen",
        "status",
        "is_bot",
    )
    search_fields = (
        "user_id",
        "username",
        "first_name",
        "last_name",
        "chat__title",
    )
    list_filter = ("status", "is_bot", "chat__type")
    readonly_fields = ("first_seen", "last_seen")
    ordering = ("-last_seen",)
    autocomplete_fields = ("chat",)

