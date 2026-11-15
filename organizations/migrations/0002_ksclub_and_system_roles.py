from django.db import migrations

SYSTEM_ROLES = [
    ("owner", "Владелец"),
    ("admin", "Администратор"),
    ("manager", "Менеджер"),
    ("coach", "Тренер"),
    ("competition_secretary", "Секретарь соревнований"),
]
DOMAINS = [("ksclub.ru", True), ("www.ksclub.ru", False)]


def forwards(apps, schema_editor):
    Organization = apps.get_model("organizations", "Organization")
    OrgDomain = apps.get_model("organizations", "OrgDomain")
    OrgRole = apps.get_model("organizations", "OrgRole")
    org, _ = Organization.objects.get_or_create(
        slug="ksclub", defaults={"name": "Канаев Ски Клаб", "kind": "club", "status": "active"}
    )
    for host, primary in DOMAINS:
        OrgDomain.objects.get_or_create(host=host, defaults={"organization": org, "is_primary": primary})
    for code, name in SYSTEM_ROLES:
        OrgRole.objects.get_or_create(organization=None, code=code, defaults={"name": name})


def backwards(apps, schema_editor):
    # Фильтрация по id и прямой SQL: исторические модели разных приложений нельзя смешивать в одном запросе.
    Organization = apps.get_model("organizations", "Organization")
    org_ids = list(Organization.objects.filter(slug="ksclub").values_list("id", flat=True))
    role_codes = [c for c, _ in SYSTEM_ROLES]
    apps.get_model("organizations", "AuditLog").objects.filter(organization_id__in=org_ids).update(organization=None)
    apps.get_model("organizations", "RoleAssignment").objects.filter(membership__organization_id__in=org_ids).delete()
    apps.get_model("organizations", "RoleAssignment").objects.filter(
        role__organization__isnull=True, role__code__in=role_codes
    ).delete()
    apps.get_model("organizations", "Membership").objects.filter(organization_id__in=org_ids).delete()
    apps.get_model("organizations", "OrgDomain").objects.filter(organization_id__in=org_ids).delete()
    apps.get_model("organizations", "OrgRole").objects.filter(organization_id__in=org_ids).delete()
    apps.get_model("organizations", "OrgRole").objects.filter(organization__isnull=True, code__in=role_codes).delete()
    if org_ids:
        with schema_editor.connection.cursor() as cursor:
            cursor.execute("DELETE FROM organizations_organization WHERE id = ANY(%s)", [org_ids])


class Migration(migrations.Migration):
    dependencies = [("organizations", "0001_initial")]
    operations = [migrations.RunPython(forwards, backwards)]
