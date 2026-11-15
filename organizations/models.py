"""Организации, членство и роли (этап 4, R1). Концепция — docs/organizations-intro.md."""

from django.conf import settings
from django.db import models


class Organization(models.Model):
    class Kind(models.TextChoices):
        CLUB = "club", "Клуб / спортшкола"
        FEDERATION = "federation", "Федерация"
        VENUE = "venue", "База / склон"
        PLATFORM = "platform", "Платформа"

    class Status(models.TextChoices):
        ACTIVE = "active", "Активна"
        SUSPENDED = "suspended", "Приостановлена"

    slug = models.SlugField("Код", max_length=64, unique=True)
    name = models.CharField("Название", max_length=255)
    kind = models.CharField("Тип", max_length=16, choices=Kind.choices, default=Kind.CLUB)
    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.ACTIVE)
    settings = models.JSONField("Настройки", default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Организация"
        verbose_name_plural = "Организации"
        ordering = ("name",)

    def __str__(self) -> str:
        return self.name


class OrgDomain(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="domains")
    host = models.CharField("Хост", max_length=255, unique=True, help_text="Без порта, в нижнем регистре")
    is_primary = models.BooleanField("Основной", default=False)
    verified_at = models.DateTimeField("Подтверждён", null=True, blank=True)

    class Meta:
        verbose_name = "Домен организации"
        verbose_name_plural = "Домены организаций"
        ordering = ("organization", "-is_primary", "host")

    def __str__(self) -> str:
        return self.host

    def save(self, *args, **kwargs):
        self.host = (self.host or "").strip().lower().rstrip(".")
        super().save(*args, **kwargs)


class OrgRole(models.Model):
    """Роль в организации. organization=None — системная роль, доступная всем организациям."""

    OWNER = "owner"
    ADMIN = "admin"
    MANAGER = "manager"
    COACH = "coach"
    SECRETARY = "competition_secretary"

    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, null=True, blank=True, related_name="custom_roles"
    )
    code = models.CharField("Код", max_length=64)
    name = models.CharField("Название", max_length=255)

    class Meta:
        verbose_name = "Роль в организации"
        verbose_name_plural = "Роли в организациях"
        constraints = [
            models.UniqueConstraint(fields=("organization", "code"), name="orgrole_unique_code_per_org"),
            models.UniqueConstraint(
                fields=("code",), condition=models.Q(organization__isnull=True), name="orgrole_unique_system_code"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Source(models.TextChoices):
    LEGACY_IMPORT = "legacy_import", "Перенос из старой схемы"
    SYNC = "sync", "Синхронизация со старыми полями"
    MANUAL = "manual", "Вручную"


class Membership(models.Model):
    class Status(models.TextChoices):
        INVITED = "invited", "Приглашён"
        PENDING = "pending", "Ждёт подтверждения"
        ACTIVE = "active", "Активен"
        SUSPENDED = "suspended", "Приостановлен"
        LEFT = "left", "Ушёл"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships")
    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.PENDING)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = "Членство"
        verbose_name_plural = "Членства"
        constraints = [models.UniqueConstraint(fields=("user", "organization"), name="membership_unique_user_org")]

    def __str__(self) -> str:
        return f"{self.user_id}@{self.organization_id}:{self.status}"


class RoleAssignment(models.Model):
    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="role_assignments")
    role = models.ForeignKey(OrgRole, on_delete=models.PROTECT, related_name="assignments")
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Назначение роли"
        verbose_name_plural = "Назначения ролей"
        constraints = [models.UniqueConstraint(fields=("membership", "role"), name="roleassignment_unique")]

    def __str__(self) -> str:
        return f"{self.membership_id}:{self.role_id}"


class PlatformRole(models.Model):
    class Role(models.TextChoices):
        GLOBAL_ADMIN = "global_admin", "Глобальный администратор"
        SUPPORT = "support", "Поддержка платформы"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="platform_roles")
    role = models.CharField(max_length=32, choices=Role.choices)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.MANUAL)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Глобальная роль"
        verbose_name_plural = "Глобальные роли"
        constraints = [models.UniqueConstraint(fields=("user", "role"), name="platformrole_unique")]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.role}"


class AuditLog(models.Model):
    organization = models.ForeignKey(Organization, on_delete=models.SET_NULL, null=True, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=64)
    target_type = models.CharField(max_length=64, blank=True)
    target_id = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Запись журнала"
        verbose_name_plural = "Журнал"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"
