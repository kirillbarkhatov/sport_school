from django.core.management.base import BaseCommand

from organizations.backfill import backfill_missing, missing_organization, organization_counts


class Command(BaseCommand):
    help = "Сверка organization у данных организаций (R2-3): агрегаты по моделям; --apply заполняет пропуски."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="записать организацию по умолчанию в пустые строки")

    def handle(self, *args, apply, **options):
        if apply:
            updated = backfill_missing()
            self.stdout.write(f"Заполнено строк: {sum(updated.values())} ({len(updated)} моделей)")
        totals: dict[str, int] = {}
        for label, by_org in organization_counts().items():
            for slug, n in by_org.items():
                totals[slug] = totals.get(slug, 0) + n
            self.stdout.write(f"{label}: " + ", ".join(f"{slug} {n}" for slug, n in sorted(by_org.items())))
        self.stdout.write("Итого: " + ", ".join(f"{slug} {n}" for slug, n in sorted(totals.items())))
        missing = missing_organization()
        self.stdout.write(f"Без организации: {sum(missing.values())}")
