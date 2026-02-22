from django.core.management.base import BaseCommand

from school.tasks import enqueue_documents_for_ai_analysis


class Command(BaseCommand):
    help = "Поставить документы в очередь AI-анализа"

    def add_arguments(self, parser):
        parser.add_argument(
            "--document-ids",
            dest="document_ids",
            help="Список ID документов через запятую (например: 10,11,12)",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Отправить в анализ даже успешно обработанные и не изменённые документы",
        )
        parser.add_argument(
            "--sync",
            action="store_true",
            help="Запустить оркестратор синхронно, без Celery-очереди",
        )

    def handle(self, *args, **options):
        document_ids = None
        if options.get("document_ids"):
            document_ids = [int(item.strip()) for item in options["document_ids"].split(",") if item.strip()]

        force = bool(options.get("force"))
        sync = bool(options.get("sync"))

        if sync:
            result = enqueue_documents_for_ai_analysis(document_ids=document_ids, force=force)
            self.stdout.write(self.style.SUCCESS(f"Готово: {result}"))
            return

        task = enqueue_documents_for_ai_analysis.delay(document_ids=document_ids, force=force)
        self.stdout.write(self.style.SUCCESS(f"Задача отправлена в очередь. task_id={task.id}"))
