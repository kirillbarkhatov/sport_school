"""Помощники миграций данных этапа 4 (R2-3): заполнение organization у исторических записей."""

from django.conf import settings
from django.db import migrations


def default_slug() -> str:
    return getattr(settings, "ORGS_DEFAULT_ORGANIZATION_SLUG", "") or "ksclub"


def backfill_organization(app_label: str, model_names: list[str]) -> migrations.RunPython:
    """Записи без организации → организация по умолчанию («ksclub»). Идемпотентно: трогает только NULL."""

    def forward(apps, schema_editor):
        Organization = apps.get_model("organizations", "Organization")
        organization = Organization.objects.filter(slug=default_slug()).first()
        if organization is None:
            return
        for name in model_names:
            model = apps.get_model(app_label, name)
            model._base_manager.filter(organization__isnull=True).update(organization=organization)

    return migrations.RunPython(forward, migrations.RunPython.noop, elidable=False)
