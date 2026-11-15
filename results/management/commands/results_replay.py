from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from results.core import engine
from results.models import ResultsEvent
from results.replay import replay
from results.services import column_mappings


class Command(BaseCommand):
    help = "Прогнать сохранённые снимки трансляции через текущий движок и показать ход событий (без записи в БД)."

    def add_arguments(self, parser):
        parser.add_argument("event_id", type=int)
        parser.add_argument("--standings", action="store_true", help="Показать итоговые места по группам")

    def handle(self, *args, event_id: int, standings: bool, **options):
        try:
            event = ResultsEvent.objects.get(pk=event_id)
        except ResultsEvent.DoesNotExist as exc:
            raise CommandError(f"Трансляция {event_id} не найдена") from exc
        snapshots = ((s.taken_at, s.sheets()) for s in event.snapshots.order_by("taken_at", "id"))
        rules = engine.FinalizeRules(timeout_sec=event.finalize_timeout_sec, max_missing=event.finalize_max_missing)
        last = None
        count = 0
        for step in replay(snapshots, mappings=column_mappings(event), rules=rules):
            count += 1
            stamp = timezone.localtime(step.at).strftime("%H:%M:%S")
            for line in step.events:
                self.stdout.write(f"{stamp}  {line}")
            last = step
        self.stdout.write(f"Снимков: {count}")
        if standings and last:
            for group in last.protocol.groups:
                self.stdout.write(f"\n{group.sheet_name} / {group.name}")
                for s in engine.standings(group):
                    value = s.value.display() or "—"
                    self.stdout.write(f"  {s.place:>3}. {s.entry.start_number:>4} {s.entry.full_name:<30} {value}")
