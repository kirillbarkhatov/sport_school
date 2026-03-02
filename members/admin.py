from django.contrib import admin

from members.models import (
    PersonDedupJob,
    PersonDuplicateCluster,
    PersonDuplicateItem,
    PersonMergeLog,
    PersonMergeRedirect,
)


@admin.register(PersonDedupJob)
class PersonDedupJobAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "status",
        "mode",
        "dry_run",
        "duplicate_clusters_found",
        "auto_merged_clusters",
        "conflicts_count",
        "created_at",
    )
    list_filter = ("status", "mode", "dry_run")
    search_fields = ("id", "error_message")


@admin.register(PersonDuplicateCluster)
class PersonDuplicateClusterAdmin(admin.ModelAdmin):
    list_display = ("id", "job", "status", "max_pair_score", "requires_manual_review", "updated_at")
    list_filter = ("status", "requires_manual_review")
    search_fields = ("reason_summary",)


@admin.register(PersonDuplicateItem)
class PersonDuplicateItemAdmin(admin.ModelAdmin):
    list_display = ("id", "cluster", "person", "aggregate_score", "is_suggested_master")
    list_filter = ("is_suggested_master",)
    search_fields = ("person__surname", "person__name")


@admin.register(PersonMergeRedirect)
class PersonMergeRedirectAdmin(admin.ModelAdmin):
    list_display = ("id", "source_person", "target_person", "job", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("source_person__surname", "target_person__surname")


@admin.register(PersonMergeLog)
class PersonMergeLogAdmin(admin.ModelAdmin):
    list_display = ("id", "master_person", "cluster", "created_by", "created_at")
    search_fields = ("master_person__surname", "notes")
