import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = (
        "Падает, если база пустая (нет применённых миграций). Защищает от старта прода на пустой БД, "
        "например после смены тома Postgres без восстановления дампа. "
        "Для первичной инициализации новой БД: ALLOW_EMPTY_DB=True."
    )

    def handle(self, *args, **options):
        if os.getenv("ALLOW_EMPTY_DB", "False") == "True":
            self.stdout.write("ALLOW_EMPTY_DB=True — проверка пропущена.")
            return
        if "django_migrations" not in connection.introspection.table_names():
            raise CommandError(
                "База пустая: таблицы django_migrations нет. Восстановите дамп "
                "(см. README, «Переезд на Postgres 18») или запустите с ALLOW_EMPTY_DB=True для новой БД."
            )
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM django_migrations")
            applied = cursor.fetchone()[0]
        self.stdout.write(f"БД инициализирована: применено миграций — {applied}.")
