from django.db import migrations


def fill_club(apps, schema_editor):
    Club = apps.get_model("school", "Club")
    FamilyMember = apps.get_model("school", "FamilyMember")
    Person = apps.get_model("school", "Person")

    club, _ = Club.objects.get_or_create(name="Канаев Ски Клаб")

    # Семейные связи со статусом "В клубе" => член клуба
    active_members = FamilyMember.objects.filter(family__status="active").select_related("person")
    person_ids = [m.person_id for m in active_members if m.person_id]
    # Обновляем только тех, у кого клуб не задан
    Person.objects.filter(id__in=person_ids, club__isnull=True).update(club=club)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("school", "0032_import_athletes_from_excels"),
    ]

    operations = [
        migrations.RunPython(fill_club, noop),
    ]
