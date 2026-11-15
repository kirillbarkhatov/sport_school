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
        if release == "r1" and flags != ("ksclub", True, "legacy"):
            errors.append(f"флаги R1 не совпадают с планом: {flags}")
        if release == "r2":
            self._check_r2(errors, notes)
        for note in notes:
            self.stdout.write(f"· {note}")
        if errors:
            for error in errors:
                self.stderr.write(f"✗ {error}")
            raise CommandError(f"stage4_check {release}: ошибок {len(errors)}")
        self.stdout.write(f"✓ stage4_check {release}: всё в порядке")

    def _check_r2(self, errors, notes):
        """R2-7: флаги, заполнение, внешние, выпускники, реестр фильтров. Права и изоляция по сценариям —
        тестами (tests/test_access_matrix.py, organizations/tests/test_scoping.py, test_externals_alumni.py)."""
        from django.contrib.auth import get_user_model

        from api import views as api_views
        from organizations.backfill import missing_organization, org_owned_models
        from organizations.externals import report as externals_report
        from organizations.models import Membership, RoleAssignment
        from organizations.scoping import LegacyResultsOrganizationMixin, OrgScopedManager

        roles, scoping = settings.ORGS_ROLES_SOURCE, settings.ORGS_ENFORCE_SCOPING
        notes.append(
            f"режимы: ORGS_ROLES_SOURCE={roles}, ORGS_ENFORCE_SCOPING={scoping}, "
            f"ORGS_LEGACY_SYNC={settings.ORGS_LEGACY_SYNC}"
        )
        if settings.ORGS_DEFAULT_ORGANIZATION_SLUG != "ksclub" or not settings.ORGS_LEGACY_SYNC:
            errors.append("R2 требует ORGS_DEFAULT_ORGANIZATION_SLUG=ksclub и ORGS_LEGACY_SYNC=True")
        if roles not in ("legacy", "memberships"):
            errors.append(f"неизвестный ORGS_ROLES_SOURCE={roles}")
        if scoping and roles != "memberships":
            errors.append(
                "ORGS_ENFORCE_SCOPING=True включается после ORGS_ROLES_SOURCE=memberships (шаги выкатки 2→3)"
            )

        missing = missing_organization()
        notes.append(f"строк без организации: {sum(missing.values())}")
        if missing:
            errors.append(f"есть данные без организации: {sorted(missing)}")

        models = org_owned_models()
        unscoped = [m._meta.label for m in models if not isinstance(m._default_manager, OrgScopedManager)]
        notes.append(f"моделей организации: {len(models)}, без фильтра по умолчанию: {len(unscoped)}")
        if unscoped:
            errors.append(f"менеджер по умолчанию без фильтра организации: {unscoped}")
        legacy_views = [v for n, v in vars(api_views).items() if n.startswith("OnlineResults") and isinstance(v, type)]
        unguarded = [v.__name__ for v in legacy_views if not issubclass(v, LegacyResultsOrganizationMixin)]
        if unguarded:
            errors.append(f"наследие трансляций без ограничения ksclub: {unguarded}")
        if "check_permissions" not in vars(api_views.UserViewSet):
            errors.append("API учётных записей без ограничения записи (D16)")

        ext = externals_report()
        notes.append(
            f"внешние: {ext['external']} (спортсменов {ext['external_athletes']}), без клуба {ext['without_club']}, "
            f"с контактами {ext['external_with_contacts']}, с документами {ext['external_with_documents']}"
        )
        if ext["unmarked_candidates"] or ext["marked_with_basis"]:
            errors.append(
                f"пометка «внешний» расходится с правилом: не помечены {ext['unmarked_candidates']}, "
                f"помечены со связью {ext['marked_with_basis']}"
            )

        left = Membership.objects.filter(status=Membership.Status.LEFT)
        # роль в ТОМ ЖЕ членстве — расхождение; активная роль в другом клубе совместима с «ушёл» здесь (D18)
        left_with_basis = left.filter(pk__in=RoleAssignment.objects.values("membership_id")).count()
        left_staff = get_user_model().objects.filter(pk__in=left.values("user_id")).filter(is_staff=True).count()
        notes.append(f"выпускников («ушёл»): {left.count()}")
        if left_with_basis or left_staff:
            errors.append(f"«ушёл» у пользователей с ролями ({left_with_basis}) или staff ({left_staff})")
