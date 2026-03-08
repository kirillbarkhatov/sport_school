from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0006_streamrun_telegram_publication_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="streamrun",
            name="telegram_finisher_last_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="streamrun",
            name="telegram_finisher_message_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]

