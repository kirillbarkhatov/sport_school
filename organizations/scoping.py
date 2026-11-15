"""Организация в данных (этап 4, R2-3, R2-4): OrgOwned, фильтрация по организации, межорганизационные связи.

- `OrgOwned` — абстрактная модель с необязательным полем `organization`. Менеджер по умолчанию
  (`objects`) добавляет к каждому запросу условие `OrgScope`; `all_objects` — без него (платформенный код:
  перенос данных, супервизор results, консоль платформы).
- `OrgScope` вычисляется при компиляции SQL, а не при создании queryset: queryset, созданные при импорте
  модуля (поля форм, DRF-вьюсеты), всё равно фильтруются по организации текущего запроса или задачи.
  ORGS_ENFORCE_SCOPING=False → условие `TRUE` (поведение R1); True → `organization_id = <текущая>`,
  а без организации (явно неизвестная или неактивная) → `FALSE`.
- Новая запись получает организацию от связанной записи-владельца (первый FK на OrgOwned), иначе из
  контекста. При ORGS_ENFORCE_SCOPING связь записей разных организаций (FK и M2M) запрещена.
- Прямой доступ по FK (`obj.family`) идёт через базовый менеджер и не фильтруется: чужая запись по FK
  появиться не может, это проверяет save().
"""

from __future__ import annotations

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import BooleanField, Expression, F
from django.db.models.signals import m2m_changed
from django.dispatch import receiver


def scoping_enabled() -> bool:
    return bool(getattr(settings, "ORGS_ENFORCE_SCOPING", False))


def scope_organization():
    """Организация для фильтрации: из контекста; контекст не задан — организация по умолчанию;
    задан явным NO_ORGANIZATION — None (данные не видны)."""
    from .context import context_is_set, default_organization, get_current_organization

    if context_is_set():
        return get_current_organization()
    return default_organization()


class OrgScope(Expression):
    """Условие WHERE «запись принадлежит текущей организации», вычисляемое при компиляции SQL."""

    conditional = True
    output_field = BooleanField()

    def __init__(self, column=None):
        super().__init__()
        self.column = column if column is not None else F("organization_id")

    def get_source_expressions(self):
        return [self.column]

    def set_source_expressions(self, exprs):
        (self.column,) = exprs

    def as_sql(self, compiler, connection):
        if not scoping_enabled():
            return "TRUE", []
        organization = scope_organization()
        if organization is None:
            return "FALSE", []
        sql, params = compiler.compile(self.column)
        return f"({sql} = %s)", [*params, organization.pk]


def _org_owned_fks(obj):
    for field in obj._meta.concrete_fields:
        if field.is_relation and field.many_to_one and issubclass(field.related_model, OrgOwned):
            yield field


def _related_organization_id(obj, field):
    value = getattr(obj, field.attname)
    if value is None:
        return None
    cached = field.get_cached_value(obj, default=None)
    if cached is not None:
        return cached.organization_id
    return field.related_model._base_manager.filter(pk=value).values_list("organization_id", flat=True).first()


def fill_organization(obj) -> None:
    if obj.organization_id is not None:
        return
    for field in _org_owned_fks(obj):
        organization_id = _related_organization_id(obj, field)
        if organization_id is not None:
            obj.organization_id = organization_id
            return
    organization = scope_organization()
    if organization is not None:
        obj.organization_id = organization.pk


def check_same_organization(obj) -> None:
    """ValidationError, если запись ссылается на запись другой организации (только при ORGS_ENFORCE_SCOPING)."""
    if not scoping_enabled() or obj.organization_id is None:
        return
    errors = {}
    for field in _org_owned_fks(obj):
        organization_id = _related_organization_id(obj, field)
        if organization_id is not None and organization_id != obj.organization_id:
            errors[field.name] = "Запись принадлежит другой организации."
    if errors:
        raise ValidationError(errors)


class OrgQuerySet(models.QuerySet):
    def for_org(self, organization=None):
        """Явная фильтрация по организации (независимо от флага)."""
        organization = organization or scope_organization()
        if organization is None:
            return self.none()
        return self.filter(organization=organization)

    def bulk_create(self, objs, *args, **kwargs):
        objs = list(objs)
        for obj in objs:
            fill_organization(obj)
            check_same_organization(obj)
        return super().bulk_create(objs, *args, **kwargs)


class OrgScopedManager(models.Manager.from_queryset(OrgQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(OrgScope())


class UnscopedManager(models.Manager.from_queryset(OrgQuerySet)):
    pass


class OrgOwned(models.Model):
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        editable=False,
        related_name="+",
        verbose_name="Организация",
    )

    objects = OrgScopedManager()
    all_objects = UnscopedManager()

    class Meta:
        abstract = True

    @classmethod
    def for_org(cls, organization=None):
        return cls.all_objects.for_org(organization)

    def save(self, *args, **kwargs):
        fill_organization(self)
        check_same_organization(self)
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and self.organization_id is not None:
            kwargs["update_fields"] = {*update_fields, "organization"}
        super().save(*args, **kwargs)


@receiver(m2m_changed, dispatch_uid="orgs_m2m_same_organization")
def _m2m_same_organization(sender, instance, action, reverse, model, pk_set, **_):
    if action != "pre_add" or not pk_set or not scoping_enabled():
        return
    if not isinstance(instance, OrgOwned) or not issubclass(model, OrgOwned):
        return
    foreign = model._base_manager.filter(pk__in=pk_set).exclude(organization_id=instance.organization_id)
    if instance.organization_id is not None and foreign.exclude(organization__isnull=True).exists():
        raise ValidationError("Нельзя связать записи разных организаций.")


def users_in_organization(queryset=None, organization=None):
    """CRM-выборка учётных записей (User — платформенная модель, OrgScope к ней не применяется, D13).

    ORGS_ENFORCE_SCOPING=False → queryset без изменений (R1). True → только пользователи с членством
    (любого статуса) в текущей организации; без организации — пусто. Поиск учётной записи для входа
    (по tg_id, токену) остаётся платформенным и этим помощником не фильтруется.
    """
    from django.contrib.auth import get_user_model

    from .models import Membership

    qs = queryset if queryset is not None else get_user_model().objects.all()
    if not scoping_enabled():
        return qs
    memberships = Membership.objects.all()
    # организация вычисляется при выполнении запроса (OrgScope), без обращения к базе при построении —
    # помощник безопасно вызывать из асинхронного кода бота
    memberships = memberships.filter(organization=organization) if organization else memberships.filter(OrgScope())
    return qs.filter(pk__in=memberships.values("user_id"))
