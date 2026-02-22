from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0039_documents"),
    ]

    operations = [
        migrations.AddField(
            model_name="competition",
            name="birth_year_from",
            field=models.PositiveIntegerField(
                blank=True,
                null=True,
                help_text="Самый ранний год рождения участников (старшие). Необязательно.",
                verbose_name="Год рождения (с)",
            ),
        ),
        migrations.AddField(
            model_name="competition",
            name="birth_year_to",
            field=models.PositiveIntegerField(
                default=2017,
                help_text="Самый поздний допустимый год рождения (минимальный возраст). Обязательно.",
                verbose_name="Год рождения (по)",
            ),
            preserve_default=False,
        ),
    ]
