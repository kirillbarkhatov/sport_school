from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="TelegramChat",
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
                    "chat_id",
                    models.BigIntegerField(unique=True, verbose_name="ID чата"),
                ),
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("private", "Личные сообщения"),
                            ("group", "Группа"),
                            ("supergroup", "Супергруппа"),
                            ("channel", "Канал"),
                            ("unknown", "Неизвестно"),
                        ],
                        default="unknown",
                        max_length=32,
                        verbose_name="Тип чата",
                    ),
                ),
                (
                    "title",
                    models.CharField(blank=True, max_length=255, verbose_name="Название"),
                ),
                (
                    "username",
                    models.CharField(
                        blank=True, max_length=255, verbose_name="Username чата"
                    ),
                ),
                (
                    "description",
                    models.TextField(blank=True, verbose_name="Описание"),
                ),
                (
                    "invite_link",
                    models.URLField(blank=True, verbose_name="Инвайт-ссылка"),
                ),
                (
                    "first_seen",
                    models.DateTimeField(
                        auto_now_add=True, verbose_name="Впервые замечен"
                    ),
                ),
                (
                    "last_seen",
                    models.DateTimeField(
                        auto_now=True, verbose_name="Последнее взаимодействие"
                    ),
                ),
                (
                    "extra_data",
                    models.JSONField(
                        blank=True, default=dict, verbose_name="Доп. данные"
                    ),
                ),
            ],
            options={
                "verbose_name": "Чат телеграм-бота",
                "verbose_name_plural": "Чаты телеграм-бота",
                "ordering": ("-last_seen",),
            },
        ),
        migrations.CreateModel(
            name="TelegramParticipant",
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
                    "user_id",
                    models.BigIntegerField(verbose_name="ID пользователя"),
                ),
                (
                    "is_bot",
                    models.BooleanField(default=False, verbose_name="Является ботом"),
                ),
                (
                    "first_name",
                    models.CharField(blank=True, max_length=255, verbose_name="Имя"),
                ),
                (
                    "last_name",
                    models.CharField(blank=True, max_length=255, verbose_name="Фамилия"),
                ),
                (
                    "username",
                    models.CharField(
                        blank=True, max_length=255, verbose_name="Username пользователя"
                    ),
                ),
                (
                    "language_code",
                    models.CharField(blank=True, max_length=12, verbose_name="Язык"),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("creator", "Создатель"),
                            ("administrator", "Администратор"),
                            ("member", "Участник"),
                            ("restricted", "Ограничен"),
                            ("left", "Покинул чат"),
                            ("kicked", "Заблокирован"),
                            ("unknown", "Неизвестно"),
                        ],
                        default="unknown",
                        max_length=32,
                        verbose_name="Статус в чате",
                    ),
                ),
                (
                    "custom_title",
                    models.CharField(
                        blank=True, max_length=255, verbose_name="Пользовательский титул"
                    ),
                ),
                (
                    "first_seen",
                    models.DateTimeField(
                        auto_now_add=True, verbose_name="Впервые замечен"
                    ),
                ),
                (
                    "last_seen",
                    models.DateTimeField(
                        auto_now=True, verbose_name="Последнее взаимодействие"
                    ),
                ),
                (
                    "extra_data",
                    models.JSONField(
                        blank=True, default=dict, verbose_name="Доп. данные"
                    ),
                ),
                (
                    "chat",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="participants",
                        to="bot.telegramchat",
                        verbose_name="Чат",
                    ),
                ),
            ],
            options={
                "verbose_name": "Собеседник телеграм-бота",
                "verbose_name_plural": "Собеседники телеграм-бота",
                "ordering": ("-last_seen", "-first_seen"),
                "unique_together": {("chat", "user_id")},
            },
        ),
        migrations.AddIndex(
            model_name="telegramparticipant",
            index=models.Index(
                fields=["chat", "username"], name="bot_tp_chat_username_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="telegramparticipant",
            index=models.Index(
                fields=["chat", "last_seen"], name="bot_tp_chat_last_seen_idx"
            ),
        ),
    ]
