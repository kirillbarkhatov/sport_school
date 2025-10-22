from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0023_alter_person_date_of_birth"),
    ]

    operations = [
        migrations.AddField(
            model_name="class",
            name="assistant_comment",
            field=models.TextField(
                blank=True,
                default="",
                verbose_name="Комментарий ассистента",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="classenrollment",
            name="assistant_comment",
            field=models.CharField(
                blank=True,
                default="",
                max_length=255,
                verbose_name="Комментарий ассистента",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="classenrollment",
            name="assistant_status",
            field=models.CharField(
                choices=[
                    ("unknown", "Неизвестно"),
                    ("confirmed", "Придёт"),
                    ("declined", "Не придёт"),
                    ("pending", "Ожидает подтверждения"),
                ],
                default="unknown",
                max_length=16,
                verbose_name="Статус от ассистента",
            ),
        ),
    ]
