from django.db import models
from django.utils.translation import gettext_lazy as _


class TelegramChat(models.Model):
    class ChatType(models.TextChoices):
        PRIVATE = "private", _("Личные сообщения")
        GROUP = "group", _("Группа")
        SUPERGROUP = "supergroup", _("Супергруппа")
        CHANNEL = "channel", _("Канал")
        UNKNOWN = "unknown", _("Неизвестно")

    chat_id = models.BigIntegerField(unique=True, verbose_name="ID чата")
    type = models.CharField(
        max_length=32,
        choices=ChatType.choices,
        default=ChatType.UNKNOWN,
        verbose_name="Тип чата",
    )
    title = models.CharField(max_length=255, blank=True, verbose_name="Название")
    username = models.CharField(
        max_length=255, blank=True, verbose_name="Username чата"
    )
    description = models.TextField(blank=True, verbose_name="Описание")
    invite_link = models.URLField(blank=True, verbose_name="Инвайт-ссылка")
    first_seen = models.DateTimeField(auto_now_add=True, verbose_name="Впервые замечен")
    last_seen = models.DateTimeField(auto_now=True, verbose_name="Последнее взаимодействие")
    extra_data = models.JSONField(default=dict, blank=True, verbose_name="Доп. данные")

    class Meta:
        verbose_name = "Чат телеграм-бота"
        verbose_name_plural = "Чаты телеграм-бота"
        ordering = ("-last_seen",)

    def __str__(self) -> str:
        title = self.title or self.username or str(self.chat_id)
        return f"{title} ({self.get_type_display()})"


class TelegramParticipant(models.Model):
    class MemberStatus(models.TextChoices):
        CREATOR = "creator", _("Создатель")
        ADMIN = "administrator", _("Администратор")
        MEMBER = "member", _("Участник")
        RESTRICTED = "restricted", _("Ограничен")
        LEFT = "left", _("Покинул чат")
        KICKED = "kicked", _("Заблокирован")
        UNKNOWN = "unknown", _("Неизвестно")

    chat = models.ForeignKey(
        TelegramChat,
        on_delete=models.CASCADE,
        related_name="participants",
        verbose_name="Чат",
    )
    user_id = models.BigIntegerField(verbose_name="ID пользователя")
    is_bot = models.BooleanField(default=False, verbose_name="Является ботом")
    first_name = models.CharField(max_length=255, blank=True, verbose_name="Имя")
    last_name = models.CharField(max_length=255, blank=True, verbose_name="Фамилия")
    username = models.CharField(
        max_length=255, blank=True, verbose_name="Username пользователя"
    )
    language_code = models.CharField(max_length=12, blank=True, verbose_name="Язык")
    status = models.CharField(
        max_length=32,
        choices=MemberStatus.choices,
        default=MemberStatus.UNKNOWN,
        verbose_name="Статус в чате",
    )
    custom_title = models.CharField(
        max_length=255, blank=True, verbose_name="Пользовательский титул"
    )
    first_seen = models.DateTimeField(auto_now_add=True, verbose_name="Впервые замечен")
    last_seen = models.DateTimeField(auto_now=True, verbose_name="Последнее взаимодействие")
    extra_data = models.JSONField(default=dict, blank=True, verbose_name="Доп. данные")

    class Meta:
        verbose_name = "Собеседник телеграм-бота"
        verbose_name_plural = "Собеседники телеграм-бота"
        unique_together = ("chat", "user_id")
        indexes = [
            models.Index(
                fields=("chat", "username"),
                name="bot_tp_chat_username_idx",
            ),
            models.Index(
                fields=("chat", "last_seen"),
                name="bot_tp_chat_last_seen_idx",
            ),
        ]
        ordering = ("-last_seen", "-first_seen")

    def __str__(self) -> str:
        name = self.display_name or str(self.user_id)
        return f"{name} в {self.chat}"

    @property
    def display_name(self) -> str:
        if self.username:
            return f"@{self.username}"
        full_name = " ".join(part for part in (self.first_name, self.last_name) if part)
        return full_name or ""
