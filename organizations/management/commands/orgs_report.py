from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from organizations.context import default_organization
from organizations.legacy import find_discrepancies
from organizations.models import Membership, PlatformRole, RoleAssignment


class Command(BaseCommand):
    help = "Сводка по организации по умолчанию: пользователи, членства, роли, расхождения со старыми полями (без ПДн)."

    def handle(self, *args, **options):
        organization = default_organization()
        if organization is None:
            self.stderr.write("Нет организации по умолчанию")
            return
        users = get_user_model().objects.count()
        memberships = Membership.objects.filter(organization=organization)
        by_status = {}
        for status in memberships.values_list("status", flat=True):
            by_status[status] = by_status.get(status, 0) + 1
        by_role = {}
        for code in RoleAssignment.objects.filter(membership__organization=organization).values_list(
            "role__code", flat=True
        ):
            by_role[code] = by_role.get(code, 0) + 1
        platform = {}
        for role in PlatformRole.objects.values_list("role", flat=True):
            platform[role] = platform.get(role, 0) + 1
        diff = find_discrepancies()
        self.stdout.write(f"Организация: {organization.slug}")
        self.stdout.write(f"Пользователей: {users}; членств: {memberships.count()}")
        self.stdout.write(f"Членства по статусам: {by_status}")
        self.stdout.write(f"Роли в организации: {by_role}")
        self.stdout.write(f"Глобальные роли: {platform}")
        self.stdout.write(
            f"Расхождения: без членства {diff.users_without_membership}, подтверждение {diff.approval_mismatch}, "
            f"группа без роли {diff.group_role_mismatch}; всего {diff.total}"
        )
