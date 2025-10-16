import logging
from typing import Iterable, Sequence

from django.conf import settings
from telegram import Bot
from telegram.ext import ContextTypes
from telegram.error import TelegramError

logger = logging.getLogger(__name__)


def get_admin_chat_ids() -> Sequence[str]:
    raw_ids: Iterable[str] = getattr(settings, "TELEGRAM_ADMIN_IDS", []) or []
    cleaned = [str(chat_id).strip() for chat_id in raw_ids if str(chat_id).strip()]
    log_chat_id = getattr(settings, "TELEGRAM_LOG_CHAT_ID", None)
    if not cleaned and log_chat_id:
        cleaned = [str(log_chat_id)]
    return cleaned


async def notify_admins_context(context: ContextTypes.DEFAULT_TYPE, message: str) -> None:
    """Send notification using handler's context."""
    await notify_admins_bot(context.bot, message)


async def notify_admins_bot(bot: Bot, message: str) -> None:
    """Send notification using bot instance."""
    for chat_id in get_admin_chat_ids():
        try:
            await bot.send_message(chat_id=int(chat_id), text=message)
        except TelegramError as exc:
            logger.exception("Не удалось отправить сообщение админу %s: %s", chat_id, exc)
