"""Текущая организация: в запросе (middleware), в задачах Celery, в боте и воркере results.

Хранится в contextvars, поэтому корректно изолирована между потоками и asyncio-задачами.
Пока организация одна, неизвестный хост и задачи без явной организации получают
ORGS_DEFAULT_ORGANIZATION_SLUG («ksclub»); на R4 запасной вариант отключается.

Явно указанная, но неактивная или несуществующая организация (домен приостановленной организации,
organization_id задачи) в «ksclub» НЕ переводится: контекст становится NO_ORGANIZATION, и при
ORGS_ENFORCE_SCOPING данные организаций не видны вовсе (T05).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from django.core.cache import cache


class _NoOrganization:
    """Контекст явно задан, но организации нет (неактивная, неизвестная). Не путать с «не задан»."""

    def __bool__(self) -> bool:
        return False

    def __repr__(self) -> str:
        return "NO_ORGANIZATION"


NO_ORGANIZATION = _NoOrganization()

_current: ContextVar[object | None] = ContextVar("current_organization", default=None)
_explicit_task_org: ContextVar[bool] = ContextVar("explicit_task_organization", default=False)
_DOMAIN_CACHE_SECONDS = 60


def get_current_organization():
    value = _current.get()
    return None if value is NO_ORGANIZATION else value


def context_is_set() -> bool:
    """Контекст задан (организацией или явным NO_ORGANIZATION)."""
    return _current.get() is not None


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
        # домен известной, но неактивной организации не переводит в организацию по умолчанию
        return organization if organization is not None else NO_ORGANIZATION
    return default_organization()


def organization_by_id(organization_id):
    from .models import Organization

    if not organization_id:
        return default_organization()
    organization = Organization.objects.filter(pk=organization_id, status=Organization.Status.ACTIVE).first()
    return organization if organization is not None else NO_ORGANIZATION


class OrganizationMiddleware:
    """request.organization по хосту; контекст очищается после ответа и при исключении."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        organization = organization_for_host(request.get_host())
        request.organization = organization or None
        token = set_current_organization(organization)
        try:
            return self.get_response(request)
        finally:
            reset_current_organization(token)


# --- Celery: у каждой задачи есть контекст организации ---------------------------------------------

# Организация передаётся заголовком сообщения ORG_HEADER (ставится при публикации из текущего контекста).
# Старые сообщения без заголовка (и без kwargs["organization_id"]) получают организацию по умолчанию —
# при одном клубе поведение прежнее (D08).
ORG_HEADER = "organization_id"
_task_tokens: dict[str, tuple] = {}


def _task_organization_id(task, kwargs):
    request = getattr(task, "request", None)
    value = getattr(request, ORG_HEADER, None) if request is not None else None
    if value is None and request is not None:
        value = (getattr(request, "headers", None) or {}).get(ORG_HEADER)
    if value is None:
        value = (kwargs or {}).get("organization_id")
    return value


def _task_prerun(task_id=None, task=None, args=None, kwargs=None, **_):
    organization_id = _task_organization_id(task, kwargs)
    if not organization_id and context_is_set():
        # eager-задача (CELERY_TASK_ALWAYS_EAGER) в том же потоке: сохраняем контекст вызывающего кода
        organization = _current.get()
    else:
        organization = organization_by_id(organization_id)
    _task_tokens[task_id] = (
        set_current_organization(organization),
        _explicit_task_org.set(bool(organization_id)),
    )


def _task_postrun(task_id=None, **_):
    tokens = _task_tokens.pop(task_id, None)
    if tokens is not None:
        reset_current_organization(tokens[0])
        _explicit_task_org.reset(tokens[1])


def _before_task_publish(headers=None, **_):
    """Задача, поставленная в контексте организации, выполнится в этой же организации."""
    if headers is None or headers.get(ORG_HEADER):
        return
    organization = get_current_organization()
    if organization is not None:
        headers[ORG_HEADER] = organization.pk


def task_has_explicit_organization() -> bool:
    return _explicit_task_org.get()


def connect_celery_signals() -> None:
    from celery.signals import before_task_publish, task_postrun, task_prerun

    task_prerun.connect(_task_prerun, dispatch_uid="organizations_task_prerun", weak=False)
    before_task_publish.connect(_before_task_publish, dispatch_uid="organizations_task_publish", weak=False)
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


# --- Периодические задачи: по одной копии на каждую активную организацию (D08) ----------------------


def for_each_organization(func):
    """Декоратор тела периодической задачи (под @shared_task).

    - Сообщение с организацией (заголовок ORG_HEADER) или прямой вызов из кода — выполняется в текущем
      контексте.
    - Сообщение без организации (beat, задачи при старте, старые сообщения очереди) — задача ставится
      заново по одной копии на каждую активную организацию с её заголовком; при одном клубе это одна
      копия для ksclub, результат прежний.
    """
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        from celery import current_task

        from .models import Organization

        task = current_task
        if task is None or task.request.called_directly or task_has_explicit_organization():
            return func(*args, **kwargs)
        call_args = args[1:] if args and args[0] is task else args
        slugs = []
        for organization in Organization.objects.filter(status=Organization.Status.ACTIVE).order_by("pk"):
            task.apply_async(args=call_args, kwargs=kwargs, headers={ORG_HEADER: organization.pk})
            slugs.append(organization.slug)
        return {"per_organization": slugs}

    return wrapper
