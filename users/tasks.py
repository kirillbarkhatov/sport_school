from __future__ import annotations

import logging
from asgiref.sync import async_to_sync
from celery import shared_task
from datetime import datetime, timedelta, time
from django.contrib.auth import get_user_model
from django.utils import timezone
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from config.settings import BOT_TOKEN
from users.models import UserPersonLink, UserPersonLinkStatus
from tg_bot.services.notifications import notify_admins_bot, notify_managers_bot

logger = logging.getLogger(__name__)

User = get_user_model()


def _format_user_brief(user, *, include_ids: bool = True) -> str:
    tg_username = f"@{user.tg_username}" if user.tg_username else "—"
    full_name = user.get_full_name() or f"{user.tg_first_name or ''} {user.tg_last_name or ''}".strip() or "—"
    phone = user.phone or "—"
    lines = []
    # if include_ids:
    #     lines.append(f"id={user.id}, tg_id={user.tg_id}")
    lines.append(f"Имя: {full_name}")
    lines.append(f"Телефон: {phone}")
    lines.append(f"Username: {tg_username}")
    return "\n".join(lines)


def _format_link_summary(link: UserPersonLink, *, include_reasons: bool = True) -> str:
    if link.suggested_person_id:
        reasons = ", ".join(link.matched_reasons or [])
        reason_suffix = f" (совпадения: {reasons})" if include_reasons and reasons else ""
        return (
            "Предварительно установлена связь с членом клуба: "
            f"{link.suggested_person.surname} {link.suggested_person.name}{reason_suffix}"
        )
    return "Предварительная связь не установлена."


def build_decision_keyboard(user_id: int, link: UserPersonLink) -> InlineKeyboardMarkup:
    buttons = []
    if link.suggested_person_id:
        buttons.append(
            [InlineKeyboardButton("✅ Подтвердить связь", callback_data=f"admin_link:confirm:{user_id}")]
        )
    buttons.append(
        [InlineKeyboardButton("🔁 Выбрать другого члена", callback_data=f"admin_link:select:{user_id}")]
    )
    buttons.append(
        [InlineKeyboardButton("➕ Добавить нового члена", callback_data=f"admin_link:create:{user_id}")]
    )
    buttons.append(
        [InlineKeyboardButton("🚫 Отклонить заявку", callback_data=f"admin_link:reject:{user_id}")]
    )
    return InlineKeyboardMarkup(buttons)


def build_pending_message_text(
    user,
    link,
    *,
    include_ids: bool,
    include_reason: bool,
    reason: str | None,
    include_reasons: bool,
) -> str:
    comment = link.user_comment or "Комментарий отсутствует"
    parts = [
        "⚠️ Пользователь ожидает подтверждения доступа",
        "",
        _format_user_brief(user, include_ids=include_ids),
        "",
        _format_link_summary(link, include_reasons=include_reasons),
        "",
        f"Комментарий пользователя: {comment}",
    ]
    # if include_reason and reason:
    #     parts.append(f"Основание уведомления: {reason}")
    return "\n".join(parts)


def _compose_daily_digest(pending_links, *, include_ids: bool, include_reasons: bool) -> str:
    lines = ["🗓️ Список пользователей на одобрении:"]
    for link in pending_links:
        user = link.user
        lines.append("")
        lines.append(_format_user_brief(user, include_ids=include_ids))
        lines.append(_format_link_summary(link, include_reasons=include_reasons))
        if link.user_comment:
            lines.append(f"Комментарий: {link.user_comment}")
    return "\n".join(lines)


def _daily_range(target_date):
    start = timezone.make_aware(datetime.combine(target_date, time.min))
    end = start + timedelta(days=1)
    return start, end


def _format_bot_activity_entry(user) -> str:
    display_name = user.display_name() if hasattr(user, "display_name") else (
        user.get_full_name() or user.email or str(user.pk)
    )
    email = user.email or "—"
    if getattr(user, "tg_username", None):
        telegram_ref = f"@{user.tg_username}"
    elif getattr(user, "tg_id", None):
        telegram_ref = f"id={user.tg_id}"
    else:
        telegram_ref = "—"
    last_seen = getattr(user, "last_bot_interaction_at", None)
    if last_seen:
        seen_display = timezone.localtime(last_seen).strftime("%H:%M")
    else:
        seen_display = "—"
    return f"• {display_name} — email: {email}, telegram: {telegram_ref}, время: {seen_display}"


@shared_task
def notify_bot_activity_daily_task() -> int:
    if not BOT_TOKEN:
        logger.debug("BOT_TOKEN отсутствует, отчёт по активности бота не отправлен.")
        return 0

    today = timezone.localdate()
    start, end = _daily_range(today)
    users_today = list(
        User.objects.filter(
            last_bot_interaction_at__gte=start,
            last_bot_interaction_at__lt=end,
        ).order_by("last_bot_interaction_at")
    )

    lines = [f"📊 Активность бота за {today.strftime('%d.%m.%Y')}"]
    lines.append("")
    if not users_today:
        lines.append("За сегодня взаимодействий не зафиксировано.")
    else:
        for user in users_today:
            lines.append(_format_bot_activity_entry(user))
        lines.append("")
        lines.append(f"Всего пользователей: {len(users_today)}")

    message = "\n".join(lines)
    async_to_sync(notify_admins_bot)(Bot(token=BOT_TOKEN), message)
    logger.info(
        "Отправлен отчёт об активности бота за %s (участников: %s)",
        today,
        len(users_today),
    )
    return len(users_today)


@shared_task
def notify_pending_user_task(
    user_id: int,
    reason: str = "initial",
    baseline: float | None = None,
) -> bool:
    if not BOT_TOKEN:
        logger.debug("BOT_TOKEN отсутствует, уведомление не отправлено.")
        return False

    try:
        user = User.objects.select_related("link", "person").get(pk=user_id)
    except User.DoesNotExist:
        logger.warning("Не удалось отправить уведомление: пользователь %s не найден", user_id)
        return False

    link = getattr(user, "link", None)
    if not link or link.status != UserPersonLinkStatus.PENDING:
        logger.info("Пользователь %s больше не ожидает подтверждения, уведомление пропущено", user_id)
        return False

    if baseline is not None and link.updated_at and link.updated_at.timestamp() > baseline:
        logger.info(
            "Уведомление о пользователе %s (reason=%s) пропущено: данные обновлены позже %.2f",
            user_id,
            reason,
            baseline,
        )
        return False

    markup = build_decision_keyboard(user.id, link)
    admin_message = build_pending_message_text(
        user,
        link,
        include_ids=True,
        include_reason=True,
        reason=reason,
        include_reasons=True,
    )
    manager_message = build_pending_message_text(
        user,
        link,
        include_ids=False,
        include_reason=False,
        reason=reason,
        include_reasons=False,
    )
    async_to_sync(notify_admins_bot)(Bot(token=BOT_TOKEN), admin_message, reply_markup=markup)
    async_to_sync(notify_managers_bot)(Bot(token=BOT_TOKEN), manager_message, reply_markup=markup)

    logger.info("Отправлено уведомление о пользователе %s (reason=%s)", user_id, reason)
    return True


@shared_task
def notify_pending_users_daily_task() -> int:
    if not BOT_TOKEN:
        return 0

    pending_links = list(
        UserPersonLink.objects.select_related("user", "suggested_person")
        .filter(status=UserPersonLinkStatus.PENDING)
        .order_by("created_at")
    )
    if not pending_links:
        return 0

    digest_admin = _compose_daily_digest(pending_links, include_ids=True, include_reasons=True)
    digest_manager = _compose_daily_digest(pending_links, include_ids=False, include_reasons=False)
    async_to_sync(notify_admins_bot)(Bot(token=BOT_TOKEN), digest_admin)
    async_to_sync(notify_managers_bot)(Bot(token=BOT_TOKEN), digest_manager)
    return len(pending_links)


def schedule_pending_user_notifications(user_id: int, baseline: float | None = None) -> None:
    notify_pending_user_task.apply_async(
        args=[user_id],
        kwargs={"reason": "через 1 минуту после /start", "baseline": baseline},
        countdown=60,
    )
    notify_pending_user_task.apply_async(
        args=[user_id],
        kwargs={"reason": "через 1 час после /start", "baseline": baseline},
        countdown=3600,
    )
