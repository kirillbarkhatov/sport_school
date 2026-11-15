"""Сверка и заполнение organization у записей OrgOwned (R2-3). Используется командой orgs_backfill и stage4_check."""

from __future__ import annotations

from django.apps import apps
from django.db.models import Count

from .context import default_organization
from .scoping import OrgOwned


def org_owned_models() -> list:
    return sorted(
        (m for m in apps.get_models() if issubclass(m, OrgOwned)),
        key=lambda m: m._meta.label,
    )


def organization_counts() -> dict[str, dict[str, int]]:
    """{модель: {slug или "—": число строк}} — агрегаты без персональных данных."""
    result = {}
    for model in org_owned_models():
        rows = model.all_objects.values("organization__slug").annotate(n=Count("pk")).order_by()
        result[model._meta.label] = {row["organization__slug"] or "—": row["n"] for row in rows}
    return result


def missing_organization() -> dict[str, int]:
    counts = {m._meta.label: m.all_objects.filter(organization__isnull=True).count() for m in org_owned_models()}
    return {label: n for label, n in counts.items() if n}


def backfill_missing() -> dict[str, int]:
    """Строки без организации → организация по умолчанию. Идемпотентно."""
    organization = default_organization()
    if organization is None:
        raise RuntimeError("нет организации по умолчанию")
    updated = {}
    for model in org_owned_models():
        n = model.all_objects.filter(organization__isnull=True).update(organization=organization)
        if n:
            updated[model._meta.label] = n
    return updated
