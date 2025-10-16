from __future__ import annotations

from datetime import timedelta

from asgiref.sync import async_to_sync
from celery import shared_task
from django.utils import timezone
from telegram import Bot

from classes.services import ensure_week_ahead_schedule
from classes.telegram import build_main_actions_keyboard, format_class_summary
from config.settings import BOT_TOKEN
from school.choices import ClassCoachStatus
from school.models import Class
from tg_bot.services.notifications import notify_coaches_bot


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
