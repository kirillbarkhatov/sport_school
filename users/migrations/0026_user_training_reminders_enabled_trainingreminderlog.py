from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0029_alter_class_equipment_alter_class_group"),
        ("users", "0006_create_core_groups"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="training_reminders_enabled",
            field=models.BooleanField(
                default=True,
                verbose_name="Напоминания о тренировках включены",
            ),
        ),
        migrations.CreateModel(
            name="TrainingReminderLog",
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
                    "sent_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Отправлено"),
                ),
                (
                    "class_instance",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="reminder_logs",
                        to="school.class",
                        verbose_name="Тренировка",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=models.CASCADE,
                        related_name="training_reminder_logs",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Пользователь",
                    ),
                ),
            ],
            options={
                "verbose_name": "Отправленное напоминание о тренировке",
                "verbose_name_plural": "Отправленные напоминания о тренировках",
            },
        ),
        migrations.AddConstraint(
            model_name="trainingreminderlog",
            constraint=models.UniqueConstraint(
                fields=("user", "class_instance"),
                name="users_trainingreminderlog_unique_user_class",
            ),
        ),
        migrations.AddIndex(
            model_name="trainingreminderlog",
            index=models.Index(
                fields=["user", "sent_at"],
                name="users_reminder_user_sent_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="trainingreminderlog",
            index=models.Index(
                fields=["class_instance", "sent_at"],
                name="users_reminder_class_sent_idx",
            ),
        ),
    ]
