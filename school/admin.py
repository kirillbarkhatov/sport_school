from django.apps import apps
from django.contrib import admin
from django.db import models as django_models


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


def register_all_models(app_label: str) -> None:
    app_config = apps.get_app_config(app_label)
    for model in app_config.get_models():
        admin_class = _build_admin_class(model)
        try:
            admin.site.register(model, admin_class)
        except admin.sites.AlreadyRegistered:
            continue


register_all_models("school")
