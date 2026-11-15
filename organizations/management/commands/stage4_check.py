from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from organizations.context import celery_signals_connected, default_organization, organization_for_host
from organizations.legacy import find_discrepancies
from organizations.models import Membership, OrgDomain, OrgRole

SYSTEM_ROLES = {OrgRole.OWNER, OrgRole.ADMIN, OrgRole.MANAGER, OrgRole.COACH, OrgRole.SECRETARY}


class Command(BaseCommand):
    help = "Проверка готовности релиза этапа 4 (docs/stage4-releases.md). Ненулевой код выхода при ошибках."

    def add_arguments(self, parser):
        parser.add_argument("--release", required=True, choices=["r1", "r2"])

    def handle(self, *args, release, **options):
        errors, notes = [], []
        organization = default_organization()
        if organization is None or organization.slug != "ksclub":
            errors.append("нет активной организации ksclub по умолчанию")
        else:
            for host in ("ksclub.ru", "www.ksclub.ru"):
                if not OrgDomain.objects.filter(host=host, organization=organization).exists():
                    errors.append(f"домен {host} не привязан к ksclub")
                if organization_for_host(host) != organization:
                    errors.append(f"{host} не разрешается в ksclub")
            users = get_user_model().objects.count()
            memberships = Membership.objects.filter(organization=organization).count()
            notes.append(f"пользователей {users}, членств {memberships}")
            if users != memberships:
                errors.append(f"членств ({memberships}) не равно числу пользователей ({users})")
            diff = find_discrepancies()
            if diff.total:
                errors.append(
                    f"расхождения со старыми полями: без членства {diff.users_without_membership}, "
                    f"подтверждение {diff.approval_mismatch}, группа без роли {diff.group_role_mismatch}"
                )
        missing_roles = SYSTEM_ROLES - set(
            OrgRole.objects.filter(organization__isnull=True).values_list("code", flat=True)
        )
        if missing_roles:
            errors.append(f"нет системных ролей: {sorted(missing_roles)}")
        if not celery_signals_connected():
            errors.append("контекст организации не подключён к задачам Celery")
        flags = (settings.ORGS_DEFAULT_ORGANIZATION_SLUG, settings.ORGS_LEGACY_SYNC, settings.ORGS_ROLES_SOURCE)
        if flags != ("ksclub", True, "legacy"):
            errors.append(f"флаги R1 не совпадают с планом: {flags}")
        if release == "r2":
            from organizations.backfill import missing_organization

            missing = missing_organization()
            notes.append(f"строк без организации: {sum(missing.values())}")
            if missing:
                errors.append(f"есть данные без организации: {sorted(missing)}")
        for note in notes:
            self.stdout.write(f"· {note}")
        if errors:
            for error in errors:
                self.stderr.write(f"✗ {error}")
            raise CommandError(f"stage4_check {release}: ошибок {len(errors)}")
        self.stdout.write(f"✓ stage4_check {release}: всё в порядке")
