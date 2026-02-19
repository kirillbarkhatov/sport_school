from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0033_fill_club_for_active"),
        ("users", "0026_user_training_reminders_enabled_trainingreminderlog"),
    ]

    operations = [
        migrations.CreateModel(
            name="UserAthleteLink",
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
                    "source",
                    models.CharField(
                        choices=[
                            ("family", "Семья"),
                            ("self_created", "Создан пользователем"),
                            ("picked_existing", "Выбран из существующих"),
                            ("other", "Другое"),
                        ],
                        default="other",
                        max_length=30,
                        verbose_name="Источник",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Создано"),
                ),
                (
                    "athlete",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_links",
                        to="school.athlete",
                        verbose_name="Спортсмен",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="athlete_links",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Пользователь",
                    ),
                ),
            ],
            options={
                "verbose_name": "Связь пользователя со спортсменом",
                "verbose_name_plural": "Связи пользователей со спортсменами",
            },
        ),
        migrations.AddConstraint(
            model_name="userathletelink",
            constraint=models.UniqueConstraint(
                fields=("user", "athlete"),
                name="users_userathletelink_unique_user_athlete",
            ),
        ),
    ]
