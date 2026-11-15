from django.core.management.base import BaseCommand

from organizations.legacy import import_legacy


class Command(BaseCommand):
    help = "Перенос is_approved, групп, is_staff, суперпользователей и TELEGRAM_*_IDS в членство и роли. Идемпотентно."

    def handle(self, *args, **options):
        report = import_legacy()
        self.stdout.write(
            f"Пользователей: {report.users}; создано членств: {report.memberships_created}, "
            f"назначений ролей: {report.roles_created}, отозвано: {report.roles_revoked}, "
            f"глобальных ролей: {report.platform_roles_created}"
        )
        self.stdout.write(f"Членства по статусам: {report.by_status}")
        self.stdout.write(f"Роли: {report.by_role}")
