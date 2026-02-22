from django.db import migrations, models
import django.utils.timezone

def default_birth_year_from():
    return django.utils.timezone.now().year - 7


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0040_competition_birth_years"),
    ]

    operations = [
        migrations.AlterField(
            model_name="competition",
            name="birth_year_from",
            field=models.PositiveIntegerField(
                default=default_birth_year_from,
                help_text="Самый ранний год рождения участников (старшие). Обязательно.",
                verbose_name="Год рождения (с)",
            ),
        ),
        migrations.AlterField(
            model_name="competition",
            name="birth_year_to",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text="Самый поздний допустимый год рождения (младшие). Оставьте пустым, если без верхней границы.",
                verbose_name="Год рождения (по)",
            ),
        ),
    ]
