from django.db import migrations


def forwards(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    Club = apps.get_model("school", "Club")
    org = Organization.objects.filter(slug="ksclub").first()
    if org is None:
        return
    Club.objects.filter(name__icontains="канаев", organization__isnull=True).update(organization=org)


def backwards(apps, schema_editor):
    Club = apps.get_model("school", "Club")
    Club.objects.filter(organization__slug="ksclub").update(organization=None)


class Migration(migrations.Migration):
    dependencies = [
        ("school", "0051_club_organization_competitionvenue_organization"),
        ("organizations", "0002_ksclub_and_system_roles"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
