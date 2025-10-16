import logging
from typing import Iterable, Sequence

from django.conf import settings
from telegram import Bot
from telegram.ext import ContextTypes
from telegram.error import TelegramError

logger = logging.getLogger(__name__)


def _clean_ids(raw_ids: Iterable[str]) -> list[str]:
    return [str(chat_id).strip() for chat_id in raw_ids if str(chat_id).strip()]


def get_admin_chat_ids() -> Sequence[str]:
    raw_ids: Iterable[str] = getattr(settings, "TELEGRAM_ADMIN_IDS", []) or []
    cleaned = _clean_ids(raw_ids)
    log_chat_id = getattr(settings, "TELEGRAM_LOG_CHAT_ID", None)
    if not cleaned and log_chat_id:
        cleaned = [str(log_chat_id)]
    return cleaned


def get_coach_chat_ids() -> Sequence[str]:
    raw_ids: Iterable[str] = getattr(settings, "TELEGRAM_COACH_IDS", []) or []
    cleaned = _clean_ids(raw_ids)
    if not cleaned:
        # В качестве резервного канала используем админов
        return get_admin_chat_ids()
    return cleaned


async def notify_admins_context(context: ContextTypes.DEFAULT_TYPE, message: str) -> None:
    """Send notification using handler's context."""
    await notify_admins_bot(context.bot, message)


async def notify_admins_bot(bot: Bot, message: str, reply_markup=None) -> None:
    """Send notification using bot instance."""
    for chat_id in get_admin_chat_ids():
        try:
            await bot.send_message(chat_id=int(chat_id), text=message, reply_markup=reply_markup)
        except TelegramError as exc:
            logger.exception("Не удалось отправить сообщение админу %s: %s", chat_id, exc)


async def notify_coaches_bot(bot: Bot, message: str, reply_markup=None) -> None:
    for chat_id in get_coach_chat_ids():
        try:
            await bot.send_message(chat_id=int(chat_id), text=message, reply_markup=reply_markup)
        except TelegramError as exc:
            logger.exception("Не удалось отправить сообщение тренеру %s: %s", chat_id, exc)
