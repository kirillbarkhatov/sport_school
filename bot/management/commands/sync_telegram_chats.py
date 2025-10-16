import asyncio
from typing import Iterable

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from telegram import Bot
from telegram.error import TelegramError

from tg_bot.user_manager import sync_chat_snapshot


class Command(BaseCommand):
    help = "Запрашивает информацию о заданных телеграм-чатах и сохраняет её в базе."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "chat_ids",
            nargs="+",
            help="Список chat_id через пробел (каналы/чаты, в которых состоит бот).",
        )

    def handle(self, *args, **options) -> None:
        chat_ids: Iterable[str] = options["chat_ids"]
        token = getattr(settings, "BOT_TOKEN", None)
        if not token:
            raise CommandError("BOT_TOKEN не настроен. Добавьте его в переменные окружения.")

        asyncio.run(self._sync_chats(chat_ids, token))

    async def _sync_chats(self, chat_ids: Iterable[str], token: str) -> None:
        async with Bot(token=token) as bot:
            for raw_id in chat_ids:
                try:
                    chat_id = int(raw_id)
                except (TypeError, ValueError):
                    self.stderr.write(self.style.ERROR(f"Некорректный chat_id: {raw_id}. Пропускаю."))
                    continue

                self.stdout.write(f"⏳ Обновляю чат {chat_id}...")

                try:
                    chat_obj = await sync_chat_snapshot(bot, chat_id)
                except TelegramError as exc:
                    self.stderr.write(
                        self.style.ERROR(f"Не удалось получить данные по {chat_id}: {exc}")
                    )
                    continue
                except Exception as exc:  # noqa: BLE001
                    self.stderr.write(
                        self.style.ERROR(f"Неожиданная ошибка для {chat_id}: {exc}")
                    )
                    continue

                self.stdout.write(
                    self.style.SUCCESS(
                        f"✅ Чат {chat_id} сохранён (ID в БД: {chat_obj.pk})."
                    )
                )
