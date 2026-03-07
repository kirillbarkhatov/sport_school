from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0003_streamrun_protocol_link"),
    ]

    operations = [
        migrations.AlterField(
            model_name="streamrun",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Ожидает запуска"),
                    ("running", "Выполняется"),
                    ("success", "Завершен"),
                    ("stopped", "Остановлен"),
                    ("failed", "Ошибка"),
                ],
                db_index=True,
                default="pending",
                max_length=20,
            ),
        ),
    ]
