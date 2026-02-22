from django.core.management.base import BaseCommand

from school.tasks import sync_documents_from_storage_task


class Command(BaseCommand):
    help = "Синхронизировать документы из storage (bucket/filesystem) в модель school.Document"

    def add_arguments(self, parser):
        parser.add_argument(
            "--prefix",
            default=None,
            help="Префикс в storage (по умолчанию из DOCS_STORAGE_SYNC_PREFIX)",
        )
        parser.add_argument(
            "--no-enqueue",
            action="store_true",
            help="Не ставить документы в AI-анализ после синхронизации",
        )
        parser.add_argument(
            "--async",
            dest="run_async",
            action="store_true",
            help="Запустить синхронизацию асинхронно через Celery",
        )

    def handle(self, *args, **options):
        kwargs = {
            "prefix": options.get("prefix"),
            "enqueue_analysis": not bool(options.get("no_enqueue")),
        }

        if options.get("run_async"):
            task = sync_documents_from_storage_task.delay(**kwargs)
            self.stdout.write(self.style.SUCCESS(f"Задача синхронизации поставлена. task_id={task.id}"))
            return

        result = sync_documents_from_storage_task(**kwargs)
        self.stdout.write(self.style.SUCCESS(f"Готово: {result}"))
