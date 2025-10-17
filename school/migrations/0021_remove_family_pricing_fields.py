from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("school", "0020_alter_athlete_level_alter_family_contact_person"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="family",
            name="base_monthly_fee",
        ),
        migrations.RemoveField(
            model_name="family",
            name="discount_type",
        ),
        migrations.RemoveField(
            model_name="family",
            name="discount_value",
        ),
        migrations.RemoveField(
            model_name="family",
            name="current_month_paid",
        ),
    ]
