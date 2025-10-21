from django.db import migrations


GROUP_NAMES = (
    "Администратор",
    "Тренер",
    "Менеджер",
)


def create_groups(apps, schema_editor):
    group_model = apps.get_model("auth", "Group")
    for name in GROUP_NAMES:
        group_model.objects.get_or_create(name=name)


def remove_groups(apps, schema_editor):
    group_model = apps.get_model("auth", "Group")
    group_model.objects.filter(name__in=GROUP_NAMES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("users", "0005_remove_user_family_user_first_bot_interaction_at_and_more"),
    ]

    operations = [
        migrations.RunPython(create_groups, remove_groups),
    ]
