from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class UserPersonLinkStatus(models.TextChoices):
    PENDING = "pending", "Ожидает подтверждения"
    APPROVED = "approved", "Связь подтверждена"
    REJECTED = "rejected", "Связь отклонена"

class User(AbstractUser):
    """Модель кастомного пользователя"""

    username = None
    email = models.EmailField(unique=True, blank=True, null=True, verbose_name="Почта")
    phone = models.CharField(
        max_length=15, blank=True, null=True, verbose_name="Телефон"
    )
    tg_id = models.PositiveBigIntegerField(unique=True, blank=True, null=True, verbose_name="ID телеграмм аккаунта")
    tg_first_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Имя в телеграмме")
    tg_last_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Фамилия в телеграмме")
    tg_username = models.CharField(max_length=100, blank=True, null=True, verbose_name="Тег в телеграмме")
    city = models.CharField(max_length=30, blank=True, null=True, verbose_name="Город")
    avatar = models.ImageField(
        upload_to="users/avatar", blank=True, null=True, verbose_name="Аватар"
    )
    token = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="Токен"
    )
    is_approved = models.BooleanField(default=False, verbose_name="Пользователь подтверждён")
    first_bot_interaction_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Первое взаимодействие с ботом",
    )
    last_bot_interaction_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Последнее взаимодействие с ботом",
    )
    training_reminders_enabled = models.BooleanField(
        default=True,
        verbose_name="Напоминания о тренировках включены",
    )
    person = models.ForeignKey(
        "school.Person",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="linked_users",
        verbose_name="Персона",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def get_accessible_family_ids(self):
        if self.is_staff or self.is_superuser:
            from school.models import Family
            return list(Family.objects.values_list("id", flat=True))

        ids = set()
        if self.person_id:
            from school.models import FamilyMember
            linked = FamilyMember.objects.filter(person_id=self.person_id).values_list("family_id", flat=True)
            ids.update(linked)
        return list(ids)

    def display_name(self):
        return self.get_full_name() or self.email or str(self.pk)

    def mark_bot_interaction(self, *, timestamp=None):
        moment = timestamp or timezone.now()
        updates = []
        if not self.first_bot_interaction_at:
            self.first_bot_interaction_at = moment
            updates.append("first_bot_interaction_at")
        if not self.last_bot_interaction_at or self.last_bot_interaction_at < moment:
            self.last_bot_interaction_at = moment
            updates.append("last_bot_interaction_at")
        if updates:
            self.save(update_fields=updates)

    @property
    def person_link(self):
        return getattr(self, "link", None)


class TrainingReminderLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="training_reminder_logs",
        verbose_name="Пользователь",
    )
    class_instance = models.ForeignKey(
        "school.Class",
        on_delete=models.CASCADE,
        related_name="reminder_logs",
        verbose_name="Тренировка",
    )
    sent_at = models.DateTimeField(auto_now_add=True, verbose_name="Отправлено")

    class Meta:
        verbose_name = "Отправленное напоминание о тренировке"
        verbose_name_plural = "Отправленные напоминания о тренировках"
        constraints = [
            models.UniqueConstraint(
                fields=("user", "class_instance"),
                name="users_trainingreminderlog_unique_user_class",
            )
        ]
        indexes = [
            models.Index(
                fields=("user", "sent_at"),
                name="users_reminder_user_sent_idx",
            ),
            models.Index(
                fields=("class_instance", "sent_at"),
                name="users_reminder_class_sent_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"Напоминание {self.user_id} → {self.class_instance_id} ({self.sent_at:%Y-%m-%d %H:%M})"


class UserPersonLink(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="link",
        verbose_name="Пользователь",
    )
    suggested_person = models.ForeignKey(
        "school.Person",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="suggested_user_links",
        verbose_name="Предложенная персона",
    )
    matched_reasons = models.JSONField(
        default=list,
        blank=True,
        verbose_name="Причины совпадения",
    )
    status = models.CharField(
        max_length=20,
        choices=UserPersonLinkStatus.choices,
        default=UserPersonLinkStatus.PENDING,
        verbose_name="Статус решения",
    )
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="person_link_decisions",
        verbose_name="Решение принял",
    )
    decided_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Дата решения",
    )
    decision_note = models.TextField(
        blank=True,
        verbose_name="Комментарий администратора",
    )
    user_comment = models.TextField(
        blank=True,
        verbose_name="Комментарий пользователя",
    )
    user_comment_updated_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Комментарий пользователя обновлён",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Предварительная связь пользователя и персоны"
        verbose_name_plural = "Предварительные связи пользователей и персон"

    def __str__(self):
        identifier = self.user.display_name()
        if self.suggested_person_id:
            return f"{identifier} → {self.suggested_person}"
        return f"{identifier} (без совпадений)"

    def reset_to_pending(self):
        self.status = UserPersonLinkStatus.PENDING
        self.decided_by = None
        self.decided_at = None
        self.decision_note = ""

    def apply_decision(self, status, *, decided_by=None, note=""):
        self.status = status
        self.decided_by = decided_by
        self.decided_at = timezone.now()
        self.decision_note = note or ""
