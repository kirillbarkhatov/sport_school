import signal

from django.core.management.base import BaseCommand

from results.worker import Supervisor


class Command(BaseCommand):
    help = "Воркер онлайн-результатов: опрашивает источники всех трансляций со статусом «Идёт»."

    def add_arguments(self, parser):
        parser.add_argument(
            "--scan-interval", type=float, default=3.0, help="Как часто проверять список трансляций, с"
        )

    def handle(self, *args, **options):
        supervisor = Supervisor(scan_interval=options["scan_interval"])

        def stop(*_):
            self.stdout.write("Останавливаю воркер…")
            supervisor.stop_event.set()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        supervisor.run()
