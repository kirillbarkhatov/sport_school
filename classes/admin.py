from django.contrib import admin
from django.db import models as django_models

from .models import TrainingTemplate


class TrainingTemplateAdmin(admin.ModelAdmin):
    list_display = [field.name for field in TrainingTemplate._meta.fields]
    search_fields = [
        field.name
        for field in TrainingTemplate._meta.fields
        if isinstance(field, (django_models.CharField, django_models.TextField))
    ]
    list_filter = [
        field.name
        for field in TrainingTemplate._meta.fields
        if field.choices
        or isinstance(field, (django_models.BooleanField, django_models.DateField, django_models.DateTimeField))
    ]


admin.site.register(TrainingTemplate, TrainingTemplateAdmin)
