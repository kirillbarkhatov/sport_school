from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0004_alter_streamrun_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="PublicStreamAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token", models.CharField(db_index=True, max_length=64, unique=True)),
                ("expires_at", models.DateTimeField(db_index=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "stream_run",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="public_access_links", to="api.streamrun"),
                ),
            ],
            options={
                "ordering": ("-created_at",),
            },
        ),
    ]
