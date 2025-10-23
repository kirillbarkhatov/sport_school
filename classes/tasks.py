from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from asgiref.sync import async_to_sync
from celery import shared_task
from django.utils import timezone
from telegram import Bot
from telegram.error import TelegramError

from classes.services import ensure_week_ahead_schedule
from classes.telegram import build_main_actions_keyboard, format_class_summary
from config.settings import BOT_TOKEN
from school.choices import ClassCoachStatus
from school.models import Class
from tg_bot.services.notifications import notify_coaches_bot
from tg_bot.services.reminders import (
    build_training_reminder_markup,
    build_training_reminder_text,
)
from tg_bot.services.training_overview import get_upcoming_trainings_for_user
from users.models import TrainingReminderLog, User

logger = logging.getLogger(__name__)


def _send_classes_to_coaches(bot: Bot, classes: list[Class], heading: str | None = None) -> None:
    if not classes:
        return
    if heading:
        async_to_sync(notify_coaches_bot)(bot, heading)
    for class_instance in classes:
        async_to_sync(notify_coaches_bot)(
            bot,
            format_class_summary(class_instance),
            reply_markup=build_main_actions_keyboard(class_instance),
        )


@shared_task
def ensure_weekly_schedule_task() -> int:
    created_classes = ensure_week_ahead_schedule()
    if not created_classes or not BOT_TOKEN:
        return len(created_classes)

    created_classes.sort(key=lambda class_instance: class_instance.date)
    bot = Bot(token=BOT_TOKEN)
    _send_classes_to_coaches(
        bot,
        created_classes,
        heading="🆕 Автоматически добавленные тренировки",
    )
    return len(created_classes)


@shared_task
def notify_today_trainings_task() -> int:
    if not BOT_TOKEN:
        return 0

    today = timezone.localdate()
    classes = list(
        Class.objects.select_related("group")
        .filter(
            date__date=today,
            coach_status__in=(ClassCoachStatus.PENDING, ClassCoachStatus.PLANNED),
        )
        .order_by("date")
    )
    if not classes:
        return 0
    bot = Bot(token=BOT_TOKEN)
    heading = f"📋 Тренировки на сегодня ({today:%d.%m})"
    _send_classes_to_coaches(bot, classes, heading)
    return len(classes)


@shared_task
def notify_tomorrow_trainings_task() -> int:
    if not BOT_TOKEN:
        return 0

    tomorrow = timezone.localdate() + timedelta(days=1)
    classes = list(
        Class.objects.select_related("group")
        .filter(
            date__date=tomorrow,
            coach_status__in=(ClassCoachStatus.PENDING, ClassCoachStatus.PLANNED),
        )
        .order_by("date")
    )
    if not classes:
        return 0
    bot = Bot(token=BOT_TOKEN)
    heading = f"📋 Тренировки на завтра ({tomorrow:%d.%m})"
    _send_classes_to_coaches(bot, classes, heading)
    return len(classes)


@shared_task
def send_training_reminders_task() -> int:
    if not BOT_TOKEN:
        return 0

    today = timezone.localdate()
    end_of_day = timezone.make_aware(datetime.combine(today, time.max))
    bot = Bot(token=BOT_TOKEN)
    sent_messages = 0

    users = (
        User.objects.filter(
            is_active=True,
            is_approved=True,
            training_reminders_enabled=True,
            tg_id__isnull=False,
        )
        .order_by("id")
    )

    for user in users:
        trainings = get_upcoming_trainings_for_user(user, until=end_of_day)
        if not trainings:
            continue

        for summary in trainings:
            if summary.start.date() != today:
                continue

            log_entry, created = TrainingReminderLog.objects.get_or_create(
                user=user,
                class_instance_id=summary.class_id,
            )
            if not created:
                continue

            text = build_training_reminder_text(summary, reminders_enabled=True)
            markup = build_training_reminder_markup(summary, reminders_enabled=True)
            try:
                bot.send_message(
                    chat_id=user.tg_id,
                    text=text,
                    reply_markup=markup,
                )
            except TelegramError as exc:
                logger.warning(
                    "Не удалось отправить напоминание пользователю %s (class=%s): %s",
                    user.pk,
                    summary.class_id,
                    exc,
                )
                log_entry.delete()
            else:
                sent_messages += 1

    return sent_messages
