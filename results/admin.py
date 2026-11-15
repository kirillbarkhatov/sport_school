from django.contrib import admin

from .models import EventLog, RawSnapshot, ResultEntry, ResultGroup, ResultsEvent, TelegramPublication


@admin.register(ResultsEvent)
class ResultsEventAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "source_kind", "competition", "last_polled_at", "created_at")
    list_filter = ("status", "source_kind")
    search_fields = ("title", "protocol_title")
    readonly_fields = ("version", "seq", "focus", "last_polled_at", "last_changed_at", "public_token")


@admin.register(ResultGroup)
class ResultGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "sheet_name", "event", "order_index", "last_result_at", "removed")
    list_filter = ("event",)


@admin.register(ResultEntry)
class ResultEntryAdmin(admin.ModelAdmin):
    list_display = ("start_number", "full_name", "club", "group", "run1_raw", "run2_raw", "athlete", "removed")
    list_filter = ("event", "link_method", "removed")
    search_fields = ("full_name", "club")
    raw_id_fields = ("athlete",)


@admin.register(EventLog)
class EventLogAdmin(admin.ModelAdmin):
    list_display = ("event", "seq", "kind", "message", "created_at")
    list_filter = ("event", "kind")


@admin.register(RawSnapshot)
class RawSnapshotAdmin(admin.ModelAdmin):
    list_display = ("event", "taken_at", "content_hash")
    list_filter = ("event",)
    exclude = ("data",)


admin.site.register(TelegramPublication)
