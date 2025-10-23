from django.core.management.base import BaseCommand
from django.utils import timezone
from asgiref.sync import async_to_sync

from telegram import Bot

from config.settings import BOT_TOKEN
from school.models import Class
from users.models import User


class Command(BaseCommand):
    help = "Send notifications about tomorrow's classes"

    def handle(self, *args, **options):
        bot = Bot(token=BOT_TOKEN)
        tomorrow = timezone.now().date() + timezone.timedelta(days=1)
        start = timezone.datetime.combine(
            tomorrow,
            timezone.datetime.min.time(),
            tzinfo=timezone.get_current_timezone(),
        )
        end = timezone.datetime.combine(
            tomorrow,
            timezone.datetime.max.time(),
            tzinfo=timezone.get_current_timezone(),
        )
        classes = Class.objects.filter(date__range=(start, end))
        if not classes:
            return

        message_lines = []
        for cls in classes:
            group_name = cls.group.name if cls.group else "Без группы"
            message_lines.append(f"{cls.date:%H:%M} {group_name}")
        message = "Завтра тренировки:\n" + "\n".join(message_lines)
        for user in User.objects.exclude(tg_id__isnull=True):
            async_to_sync(bot.send_message)(chat_id=user.tg_id, text=message)
