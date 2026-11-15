"""Текущая организация: в запросе (middleware), в задачах Celery, в боте и воркере results.

Хранится в contextvars, поэтому корректно изолирована между потоками и asyncio-задачами.
Пока организация одна, неизвестный хост и задачи без явной организации получают
ORGS_DEFAULT_ORGANIZATION_SLUG («ksclub»); на R4 запасной вариант отключается.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from django.core.cache import cache

_current: ContextVar[object | None] = ContextVar("current_organization", default=None)
_DOMAIN_CACHE_SECONDS = 60


def get_current_organization():
    return _current.get()


def set_current_organization(organization):
    """Возвращает токен для reset_current_organization."""
    return _current.set(organization)


def reset_current_organization(token) -> None:
    _current.reset(token)


@contextmanager
def organization_context(organization):
    token = set_current_organization(organization)
    try:
        yield organization
    finally:
        reset_current_organization(token)


def default_organization():
    from .models import Organization

    slug = getattr(settings, "ORGS_DEFAULT_ORGANIZATION_SLUG", "")
    if not slug:
        return None
    return Organization.objects.filter(slug=slug, status=Organization.Status.ACTIVE).first()


def normalize_host(host: str) -> str:
    return (host or "").split(":")[0].strip().lower().rstrip(".")


def organization_for_host(host: str):
    """Организация по хосту; неизвестный хост — организация по умолчанию (пока флаг задан)."""
    from .models import Organization, OrgDomain

    host = normalize_host(host)
    cache_key = f"orgs:host:{host}"
    org_id = cache.get(cache_key)
    if org_id is None:
        domain = OrgDomain.objects.select_related("organization").filter(host=host).first()
        org_id = domain.organization_id if domain else 0
        cache.set(cache_key, org_id, _DOMAIN_CACHE_SECONDS)
    if org_id:
        organization = Organization.objects.filter(pk=org_id, status=Organization.Status.ACTIVE).first()
        if organization is not None:
            return organization
    return default_organization()


def organization_by_id(organization_id):
    from .models import Organization

    if not organization_id:
        return default_organization()
    return Organization.objects.filter(pk=organization_id).first() or default_organization()


class OrganizationMiddleware:
    """request.organization по хосту; контекст очищается после ответа и при исключении."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        organization = organization_for_host(request.get_host())
        request.organization = organization
        token = set_current_organization(organization)
        try:
            return self.get_response(request)
        finally:
            reset_current_organization(token)


# --- Celery: у каждой задачи есть контекст организации ---------------------------------------------

_task_tokens: dict[str, object] = {}


def _task_prerun(task_id=None, task=None, args=None, kwargs=None, **_):
    organization = organization_by_id((kwargs or {}).get("organization_id"))
    _task_tokens[task_id] = set_current_organization(organization)


def _task_postrun(task_id=None, **_):
    token = _task_tokens.pop(task_id, None)
    if token is not None:
        reset_current_organization(token)


def connect_celery_signals() -> None:
    from celery.signals import task_postrun, task_prerun

    task_prerun.connect(_task_prerun, dispatch_uid="organizations_task_prerun", weak=False)
    task_postrun.connect(_task_postrun, dispatch_uid="organizations_task_postrun", weak=False)


def celery_signals_connected() -> bool:
    from celery.signals import task_postrun, task_prerun

    return (
        bool(task_prerun.has_listeners())
        and bool(task_postrun.has_listeners())
        and any(
            uid == "organizations_task_prerun" or (isinstance(uid, tuple) and "organizations_task_prerun" in uid)
            for uid, _ in task_prerun.receivers
        )
    )


def activate_default_organization():
    """Для долгоживущих процессов без запроса (бот, воркер results): контекст по умолчанию."""
    organization = default_organization()
    set_current_organization(organization)
    return organization
