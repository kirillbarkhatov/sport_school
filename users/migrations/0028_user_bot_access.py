from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0027_userathletelink"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="bot_access",
            field=models.CharField(
                choices=[("lite", "Только авторизация"), ("full", "Полный доступ")],
                default="lite",
                max_length=10,
                verbose_name="Доступ к функциям бота",
            ),
        ),
    ]
