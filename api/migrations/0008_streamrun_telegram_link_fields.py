from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("api", "0007_streamrun_telegram_finisher_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="streamrun",
            name="telegram_link_last_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="streamrun",
            name="telegram_link_message_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
    ]

