from django.core.management.base import BaseCommand

from organizations.alumni import mark_alumni


class Command(BaseCommand):
    help = "Семьи-выпускники (R2-5): членство связанных пользователей → «ушёл» по консервативному правилу."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="записать изменения (по умолчанию пробный прогон)")

    def handle(self, *args, apply, **options):
        report = mark_alumni(apply=apply)
        mode = "применено" if apply else "пробный прогон"
        self.stdout.write(
            f"[{mode}] семей-выпускников: {report.alumni_families}; к переводу в «ушёл»: {report.candidates}; "
            f"переведено: {report.marked}; оставлены активными (есть основание): {report.kept_with_basis}"
        )
