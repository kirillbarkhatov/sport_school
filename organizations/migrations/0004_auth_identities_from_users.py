"""R3-1: способы входа из User.tg_id и email. Идемпотентно; откат удаляет только перенесённое этой миграцией."""

from django.db import migrations

AUTOGEN_EMAIL_SUFFIX = "@autogen.local"  # технический email бота — не способ входа


def forwards(apps, schema_editor):
    User = apps.get_model("users", "User")
    AuthIdentity = apps.get_model("organizations", "AuthIdentity")
    taken = set(AuthIdentity.objects.values_list("kind", "value"))
    new = []
    for user_id, tg_id, email in User.objects.values_list("id", "tg_id", "email").order_by("id"):
        if tg_id and ("telegram", str(tg_id)) not in taken:
            new.append(AuthIdentity(user_id=user_id, kind="telegram", value=str(tg_id), source="legacy_import"))
            taken.add(("telegram", str(tg_id)))
        normalized = (email or "").strip().lower()
        if normalized and not normalized.endswith(AUTOGEN_EMAIL_SUFFIX) and ("email", normalized) not in taken:
            new.append(AuthIdentity(user_id=user_id, kind="email", value=normalized, source="legacy_import"))
            taken.add(("email", normalized))
    AuthIdentity.objects.bulk_create(new)


def backwards(apps, schema_editor):
    apps.get_model("organizations", "AuthIdentity").objects.filter(source="legacy_import").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("organizations", "0003_r3_auth_identity_invitations"),
        ("users", "0029_trainingreminderlog_organization_and_more"),
    ]
    operations = [migrations.RunPython(forwards, backwards)]
