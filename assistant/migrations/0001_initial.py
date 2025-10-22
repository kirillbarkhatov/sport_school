from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("school", "0023_alter_person_date_of_birth"),
    ]

    operations = [
        migrations.CreateModel(
            name="ServiceAccount",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100, verbose_name="Название")),
                (
                    "slug",
                    models.SlugField(
                        max_length=50,
                        unique=True,
                        verbose_name="Идентификатор",
                    ),
                ),
                (
                    "key",
                    models.CharField(
                        default="",
                        help_text="Используется в заголовке JWT для идентификации секрета.",
                        max_length=64,
                        unique=True,
                        verbose_name="Ключ (kid)",
                    ),
                ),
                (
                    "secret",
                    models.CharField(
                        help_text="Используется для подписи JWT (HS256).",
                        max_length=128,
                        verbose_name="Секрет",
                    ),
                ),
                (
                    "issuer",
                    models.CharField(
                        default="sport-school",
                        max_length=100,
                        verbose_name="Issuer",
                    ),
                ),
                (
                    "audience",
                    models.CharField(
                        default="ai-assistant",
                        max_length=100,
                        verbose_name="Audience",
                    ),
                ),
                (
                    "lifetime_seconds",
                    models.PositiveIntegerField(
                        default=300,
                        verbose_name="Срок действия токена (сек.)",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(default=True, verbose_name="Активен"),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name="Создан",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        verbose_name="Обновлён",
                    ),
                ),
            ],
            options={
                "verbose_name": "Сервисный аккаунт",
                "verbose_name_plural": "Сервисные аккаунты",
                "ordering": ("name",),
            },
        ),
        migrations.CreateModel(
            name="AssistantSyncLog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "direction",
                    models.CharField(
                        choices=[
                            ("outgoing", "Исходящий"),
                            ("incoming", "Входящий"),
                        ],
                        max_length=16,
                        verbose_name="Направление",
                    ),
                ),
                (
                    "event_type",
                    models.CharField(max_length=64, verbose_name="Тип события"),
                ),
                (
                    "request_url",
                    models.URLField(
                        blank=True,
                        max_length=500,
                        verbose_name="URL запроса",
                    ),
                ),
                (
                    "status_code",
                    models.PositiveIntegerField(
                        blank=True,
                        null=True,
                        verbose_name="HTTP статус",
                    ),
                ),
                (
                    "request_payload",
                    models.JSONField(
                        blank=True,
                        null=True,
                        verbose_name="Отправленные данные",
                    ),
                ),
                (
                    "response_payload",
                    models.JSONField(
                        blank=True,
                        null=True,
                        verbose_name="Полученные данные",
                    ),
                ),
                (
                    "error_message",
                    models.TextField(blank=True, verbose_name="Ошибка"),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name="Создан",
                    ),
                ),
                (
                    "service_account",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="sync_logs",
                        to="assistant.serviceaccount",
                        verbose_name="Сервисный аккаунт",
                    ),
                ),
            ],
            options={
                "verbose_name": "Лог обмена с ассистентом",
                "verbose_name_plural": "Логи обмена с ассистентом",
                "ordering": ("-created_at",),
            },
        ),
        migrations.CreateModel(
            name="AssistantUnmatchedParticipant",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "full_name",
                    models.CharField(
                        blank=True,
                        max_length=255,
                        verbose_name="ФИО",
                    ),
                ),
                (
                    "phone",
                    models.CharField(
                        blank=True,
                        max_length=32,
                        verbose_name="Телефон",
                    ),
                ),
                (
                    "comment",
                    models.TextField(blank=True, verbose_name="Комментарий"),
                ),
                (
                    "raw_payload",
                    models.JSONField(
                        blank=True,
                        null=True,
                        verbose_name="Исходные данные",
                    ),
                ),
                (
                    "received_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name="Получено",
                    ),
                ),
                (
                    "service_account",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="unmatched_participants",
                        to="assistant.serviceaccount",
                        verbose_name="Сервисный аккаунт",
                    ),
                ),
                (
                    "training",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="assistant_unmatched_participants",
                        to="school.class",
                        verbose_name="Занятие",
                    ),
                ),
            ],
            options={
                "verbose_name": "Не сопоставленный участник",
                "verbose_name_plural": "Не сопоставленные участники",
                "ordering": ("-received_at",),
            },
        ),
    ]
