from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0010_alter_athlete_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="competition",
            name="competition_type",
            field=models.CharField(
                choices=[("sport", "Спортивные"), ("physical", "Физкультурные")],
                default="sport",
                max_length=20,
                verbose_name="Тип соревнований",
            ),
        ),
        migrations.AddField(
            model_name="competition",
            name="discipline",
            field=models.CharField(
                blank=True,
                max_length=100,
                null=True,
                verbose_name="Дисциплина",
            ),
        ),
    ]
