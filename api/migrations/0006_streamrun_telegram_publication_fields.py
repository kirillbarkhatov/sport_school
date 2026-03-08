from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("bot", "0002_telegramparticipant_linked_at_and_more"),
        ("api", "0005_publicstreamaccess"),
    ]

    operations = [
        migrations.AddField(
            model_name="streamrun",
            name="telegram_active_group_key",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="streamrun",
            name="telegram_active_message_id",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="streamrun",
            name="telegram_active_run_stage",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="streamrun",
            name="telegram_channel",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="online_results_stream_runs",
                to="bot.telegramchat",
            ),
        ),
        migrations.AddField(
            model_name="streamrun",
            name="telegram_last_error",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="streamrun",
            name="telegram_last_message_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="streamrun",
            name="telegram_publish_enabled",
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]

