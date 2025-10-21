from __future__ import annotations

import logging
from datetime import timedelta

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from telegram import Update
from telegram.ext import ContextTypes

from users.models import User
from users.tasks import notify_pending_user_task
from tg_bot.services.user_sync import update_user_comment_async

logger = logging.getLogger(__name__)

COMMENT_STATE_KEY = "awaiting_comment"


async def _fetch_upcoming_schedule_text() -> str:
    from school.models import Class

    now = timezone.now()
    week_later = now + timedelta(days=7)
    upcoming = await sync_to_async(list, thread_sensitive=True)(
        Class.objects.select_related("group")
        .filter(date__range=(now, week_later))
        .order_by("date")
    )
    if not upcoming:
        return "Ближайшие занятия не найдены."

    lines = ["Ближайшие занятия:"]
    for class_instance in upcoming:
        lines.append(f"• {class_instance.date:%d.%m %H:%M} — {class_instance.group.name}")
    return "\n".join(lines)


async def _fetch_family_summary(tg_id: int | None) -> str:
    if not tg_id:
        return "Не удалось определить пользователя."

    user = await sync_to_async(
        User.objects.select_related("person").filter(tg_id=tg_id).first,
        thread_sensitive=True,
    )()
    if not user or not user.person_id:
        return (
            "Мы пока не нашли в системе данных о вашей семье."
            " Дождитесь подтверждения администратора или дополните информацию о себе."
        )

    from school.models import FamilyMember

    memberships = await sync_to_async(list, thread_sensitive=True)(
        FamilyMember.objects.select_related("family")
        .filter(person_id=user.person_id)
    )
    if not memberships:
        return (
            "Ваш профиль связан с персоной, но семьи пока не найдены."
            " Обратитесь к администратору для уточнения."
        )

    lines = ["Семьи, к которым вы привязаны:"]
    for membership in memberships:
        family = membership.family
        if not family:
            continue
        lines.append(f"• {family.family_name or 'Семья без названия'} (роль: {membership.get_relation_display()})")
    return "\n".join(lines)


async def handle_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data or ""
    await query.answer()

    if data == "user:comment":
        context.user_data[COMMENT_STATE_KEY] = True
        await query.message.reply_text(
            "Напишите сообщение – мы передадим его администратору."
        )
        return

    if data == "user:schedule":
        text = await _fetch_upcoming_schedule_text()
        await query.message.reply_text(text)
        return

    if data == "user:family":
        text = await _fetch_family_summary(update.effective_user.id)
        await query.message.reply_text(text)
        return

    if data == "user:family_edit":
        url = settings.SITE_BASE_URL.rstrip("/") + "/members/"
        await query.message.reply_text(
            "Для обновления данных семьи воспользуйтесь веб-интерфейсом:"
            f" {url} (раздел «Члены клуба»)."
        )
        return

    await query.message.reply_text("Действие пока недоступно.")


async def handle_comment_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not context.user_data.get(COMMENT_STATE_KEY):
        return False

    comment = (update.effective_message.text or "").strip()
    if not comment:
        await update.effective_message.reply_text("Пожалуйста, отправьте текстовый комментарий.")
        return True

    user = await sync_to_async(
        User.objects.select_related("link").filter(tg_id=update.effective_user.id).first,
        thread_sensitive=True,
    )()
    if not user:
        await update.effective_message.reply_text("Не удалось найти ваш профиль. Нажмите /start и попробуйте снова.")
        context.user_data.pop(COMMENT_STATE_KEY, None)
        return True

    await update_user_comment_async(user, comment)
    notify_pending_user_task.delay(user.id, reason="пользователь обновил комментарий")
    context.user_data.pop(COMMENT_STATE_KEY, None)
    await update.effective_message.reply_text("Комментарий передан администратору.")
    logger.info("Пользователь %s оставил комментарий для администратора", user.id)
    return True
