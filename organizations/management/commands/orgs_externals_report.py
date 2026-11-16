from django.core.management.base import BaseCommand

from organizations.externals import mark_externals, report

LABELS = {
    "people": "людей всего",
    "external": "внешних",
    "external_athletes": "из них спортсменов",
    "without_club": "без клуба (не помечаются, ручной разбор)",
    "external_with_contacts": "внешних с контактами",
    "external_with_documents": "внешних с документами",
    "unmarked_candidates": "подходят под правило, но не помечены",
    "marked_with_basis": "помечены, но есть связь с организацией",
}


class Command(BaseCommand):
    help = "Отчёт по внешним спортсменам (R2-5): только агрегаты. --apply помечает подходящих под правило."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="пометить подходящих под правило")

    def handle(self, *args, apply, **options):
        if apply:
            self.stdout.write(f"Помечено: {mark_externals()}")
        for key, value in report().items():
            self.stdout.write(f"{LABELS[key]}: {value}")
