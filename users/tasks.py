from __future__ import annotations

import logging
from asgiref.sync import async_to_sync
from celery import shared_task
from django.contrib.auth import get_user_model
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from config.settings import BOT_TOKEN
from users.models import UserPersonLink, UserPersonLinkStatus
from tg_bot.services.notifications import notify_admins_bot, notify_managers_bot

logger = logging.getLogger(__name__)

User = get_user_model()


def _format_user_brief(user) -> str:
    tg_username = f"@{user.tg_username}" if user.tg_username else "—"
    full_name = user.get_full_name() or f"{user.tg_first_name or ''} {user.tg_last_name or ''}".strip() or "—"
    phone = user.phone or "—"
    return (
        f"id={user.id}, tg_id={user.tg_id}\n"
        f"Имя: {full_name}\n"
        f"Телефон: {phone}\n"
        f"Username: {tg_username}"
    )


def _format_link_summary(link: UserPersonLink) -> str:
    if link.suggested_person_id:
        reasons = ", ".join(link.matched_reasons or [])
        return (
            "Предварительно установлена связь с членом клуба: "
            f"{link.suggested_person.surname} {link.suggested_person.name}"
            + (f" (совпадения: {reasons})" if reasons else "")
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


def _compose_pending_message(user, link, reason: str) -> tuple[str, InlineKeyboardMarkup | None]:
    comment = link.user_comment or "Комментарий отсутствует"
    parts = [
        "⚠️ Пользователь ожидает подтверждения доступа",
        "",
        _format_user_brief(user),
        "",
        _format_link_summary(link),
        "",
        f"Комментарий пользователя: {comment}",
        f"Основание уведомления: {reason}",
    ]
    markup = build_decision_keyboard(user.id, link)
    return "\n".join(parts), markup


def _compose_daily_digest(pending_links) -> str:
    lines = ["🗓️ Список пользователей на одобрении:"]
    for link in pending_links:
        user = link.user
        lines.append("")
        lines.append(_format_user_brief(user))
        lines.append(_format_link_summary(link))
        if link.user_comment:
            lines.append(f"Комментарий: {link.user_comment}")
    return "\n".join(lines)


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

    message, markup = _compose_pending_message(user, link, reason)
    async_to_sync(notify_admins_bot)(Bot(token=BOT_TOKEN), message, reply_markup=markup)
    async_to_sync(notify_managers_bot)(Bot(token=BOT_TOKEN), message, reply_markup=markup)

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

    digest = _compose_daily_digest(pending_links)
    async_to_sync(notify_admins_bot)(Bot(token=BOT_TOKEN), digest)
    async_to_sync(notify_managers_bot)(Bot(token=BOT_TOKEN), digest)
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
