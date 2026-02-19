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
            name="CompetitionApplication",
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
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Создано"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Обновлено"),
                ),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="applications",
                        to="school.competition",
                        verbose_name="Соревнование",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="competition_applications",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Пользователь",
                    ),
                ),
            ],
            options={
                "verbose_name": "Заявка пользователя",
                "verbose_name_plural": "Заявки пользователей",
            },
        ),
        migrations.CreateModel(
            name="CompetitionApplicationLink",
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
                    "token",
                    models.CharField(max_length=100, unique=True, verbose_name="Токен"),
                ),
                (
                    "expires_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="Приём заявок до",
                    ),
                ),
                (
                    "is_active",
                    models.BooleanField(default=True, verbose_name="Активна"),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="Создано"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="Обновлено"),
                ),
                (
                    "competition",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="application_link",
                        to="school.competition",
                        verbose_name="Соревнование",
                    ),
                ),
            ],
            options={
                "verbose_name": "Ссылка на заявку",
                "verbose_name_plural": "Ссылки на заявки",
            },
        ),
        migrations.AddField(
            model_name="competitionentry",
            name="application",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="entries",
                to="school.competitionapplication",
                verbose_name="Заявка пользователя",
            ),
        ),
        migrations.AddConstraint(
            model_name="competitionapplication",
            constraint=models.UniqueConstraint(
                fields=("competition", "user"),
                name="school_competitionapplication_unique_competition_user",
            ),
        ),
    ]
