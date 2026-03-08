from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0009_streamrun_source_and_telegram_resume"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="streamrun",
            options={"ordering": ("-last_requested_at", "-created_at")},
        ),
    ]
