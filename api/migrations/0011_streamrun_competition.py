from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0050_person_telegram_id"),
        ("api", "0010_alter_streamrun_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="streamrun",
            name="competition",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="online_result_stream_runs",
                to="school.competition",
            ),
        ),
    ]
