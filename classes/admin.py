from django.contrib import admin

from .models import TrainingTemplate


@admin.register(TrainingTemplate)
class TrainingTemplateAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "group",
        "get_day_of_week_display",
        "start_time",
        "season_start_month",
        "season_end_month",
        "is_active",
    )
    list_filter = (
        "group",
        "day_of_week",
        "season_start_month",
        "season_end_month",
        "training_type",
        "location",
        "is_active",
    )
    search_fields = ("name", "group__name")
