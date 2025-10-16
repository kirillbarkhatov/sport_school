from django.db import models
from django.utils.translation import gettext_lazy as _


class WhatsAppChat(models.Model):
    name = models.CharField(
        max_length=255,
        unique=True,
        verbose_name=_("Название группы"),
    )
    last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Последняя синхронизация"),
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Создано"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Обновлено"))

    class Meta:
        verbose_name = _("Группа WhatsApp")
        verbose_name_plural = _("Группы WhatsApp")
        ordering = ("-last_synced_at", "-updated_at")

    def __str__(self) -> str:
        return self.name


class WhatsAppChatMember(models.Model):
    chat = models.ForeignKey(
        WhatsAppChat,
        on_delete=models.CASCADE,
        related_name="members",
        verbose_name=_("Группа"),
    )
    member_id = models.CharField(max_length=255, verbose_name=_("Идентификатор участника"))
    phone = models.CharField(
        max_length=64,
        blank=True,
        verbose_name=_("Телефон"),
    )
    push_name = models.CharField(
        max_length=255,
        blank=True,
        verbose_name=_("Отображаемое имя"),
    )
    is_admin = models.BooleanField(default=False, verbose_name=_("Администратор"))
    is_super_admin = models.BooleanField(default=False, verbose_name=_("Главный администратор"))
    is_active = models.BooleanField(default=True, verbose_name=_("Активный участник"))
    first_seen_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_("Впервые замечен"),
    )
    last_seen_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Последний раз замечен"),
    )
    left_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name=_("Дата выхода"),
    )

    class Meta:
        verbose_name = _("Участник группы WhatsApp")
        verbose_name_plural = _("Участники групп WhatsApp")
        unique_together = ("chat", "member_id")
        indexes = [
            models.Index(fields=("chat", "member_id"), name="wa_mem_chat_member_id_idx"),
            models.Index(fields=("chat", "phone"), name="wa_mem_chat_phone_idx"),
            models.Index(fields=("is_active",), name="wa_mem_active_idx"),
        ]
        ordering = ("chat", "-last_seen_at", "-first_seen_at")

    def __str__(self) -> str:
        return f"{self.display_name or self.member_id} ({self.chat.name})"

    @property
    def display_name(self) -> str:
        return self.push_name or self.phone
