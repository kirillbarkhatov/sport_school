from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0030_alter_athlete_rank"),
    ]

    operations = [
        migrations.CreateModel(
            name="Club",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150, unique=True, verbose_name="Название клуба")),
            ],
            options={
                "verbose_name": "Клуб",
                "verbose_name_plural": "Клубы",
            },
        ),
        migrations.AddField(
            model_name="person",
            name="club",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="members",
                to="school.club",
                verbose_name="Клуб",
            ),
        ),
        migrations.AddField(
            model_name="competition",
            name="start_date",
            field=models.DateField(blank=True, null=True, verbose_name="Дата начала"),
        ),
        migrations.AddField(
            model_name="competition",
            name="end_date",
            field=models.DateField(blank=True, null=True, verbose_name="Дата окончания"),
        ),
        migrations.AlterField(
            model_name="competition",
            name="date",
            field=models.DateField(blank=True, null=True, verbose_name="Дата проведения"),
        ),
        migrations.AlterField(
            model_name="competitionentry",
            name="result",
            field=models.CharField(blank=True, max_length=100, null=True, verbose_name="Результат участия"),
        ),
    ]
