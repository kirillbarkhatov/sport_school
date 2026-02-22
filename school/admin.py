from django.apps import apps
from django.contrib import admin
from django.db import models as django_models

from .models import Document, DocumentAIAnalysis
from .tasks import enqueue_documents_for_ai_analysis


class AnalysisEntityFilter(admin.SimpleListFilter):
    title = "Сущность"
    parameter_name = "entity"

    def lookups(self, request, model_admin):
        return (
            ("competition", "Соревнование"),
            ("athlete", "Спортсмен"),
            ("mixed", "Смешанная"),
            ("other", "Без привязки"),
        )

    def queryset(self, request, queryset):
        value = self.value()
        if value == "competition":
            return queryset.filter(document__competition_links__isnull=False, document__athlete_links__isnull=True)
        if value == "athlete":
            return queryset.filter(document__competition_links__isnull=True, document__athlete_links__isnull=False)
        if value == "mixed":
            return queryset.filter(document__competition_links__isnull=False, document__athlete_links__isnull=False)
        if value == "other":
            return queryset.filter(document__competition_links__isnull=True, document__athlete_links__isnull=True)
        return queryset


@admin.action(description="Поставить выбранные документы в очередь AI-анализа")
def enqueue_ai_analysis_action(modeladmin, request, queryset):
    document_ids = list(queryset.values_list("id", flat=True))
    enqueue_documents_for_ai_analysis.delay(document_ids=document_ids)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "original_name",
        "mime_type",
        "size",
        "uploaded_by",
        "created_at",
        "updated_at",
    )
    list_filter = ("created_at", "updated_at")
    search_fields = ("original_name", "description")
    actions = [enqueue_ai_analysis_action]


@admin.register(DocumentAIAnalysis)
class DocumentAIAnalysisAdmin(admin.ModelAdmin):
    list_display = (
        "document",
        "entity_view",
        "status",
        "doc_type",
        "confidence",
        "key_fields",
        "analyzed_at",
        "error_stage",
        "error_short",
    )
    list_filter = (
        "status",
        "doc_type",
        "analyzed_at",
        AnalysisEntityFilter,
    )
    search_fields = (
        "document__original_name",
        "request_id",
        "title",
        "error_message",
    )
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="Сущность")
    def entity_view(self, obj):
        competitions = [link.competition.name for link in obj.document.competition_links.select_related("competition").all()]
        athletes = [
            f"{link.athlete.person.surname} {link.athlete.person.name}"
            for link in obj.document.athlete_links.select_related("athlete__person").all()
        ]
        chunks = []
        if competitions:
            chunks.append("🏆 " + ", ".join(competitions[:2]))
        if athletes:
            chunks.append("👤 " + ", ".join(athletes[:2]))
        return " | ".join(chunks) or "-"

    @admin.display(description="Ключевые поля")
    def key_fields(self, obj):
        extracted = obj.extracted or {}
        if obj.doc_type == DocumentAIAnalysis.DocType.COMPETITION_GENERAL:
            return f"{extracted.get('competition_name') or '-'} / {extracted.get('discipline') or '-'}"
        if obj.doc_type == DocumentAIAnalysis.DocType.ATHLETE_SPECIFIC:
            person = extracted.get("person") or {}
            person_name = person.get("full_name") or " ".join(
                part for part in [person.get("last_name"), person.get("first_name"), person.get("middle_name")]
                if part
            ).strip()
            return f"{person_name or '-'} / {extracted.get('document_name') or '-'}"
        if obj.doc_type == DocumentAIAnalysis.DocType.OTHER:
            return extracted.get("summary") or "-"
        return "-"

    @admin.display(description="Ошибка")
    def error_short(self, obj):
        if not obj.error_message:
            return "-"
        return obj.error_message[:120]


def _build_admin_class(model):
    field_names = [field.name for field in model._meta.fields]
    search_fields = [
        field.name
        for field in model._meta.fields
        if isinstance(field, (django_models.CharField, django_models.TextField))
    ]
    list_filter = [
        field.name
        for field in model._meta.fields
        if field.choices
        or isinstance(field, (django_models.BooleanField, django_models.DateField, django_models.DateTimeField))
    ]
    return type(
        f"{model.__name__}Admin",
        (admin.ModelAdmin,),
        {
            "list_display": field_names,
            "search_fields": search_fields,
            "list_filter": list_filter,
        },
    )


def register_all_models(app_label: str, *, skip_model_names: set[str] | None = None) -> None:
    app_config = apps.get_app_config(app_label)
    skip_model_names = skip_model_names or set()
    for model in app_config.get_models():
        if model.__name__ in skip_model_names:
            continue
        admin_class = _build_admin_class(model)
        try:
            admin.site.register(model, admin_class)
        except admin.sites.AlreadyRegistered:
            continue


register_all_models("school", skip_model_names={"Document", "DocumentAIAnalysis"})
