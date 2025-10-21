import logging
from typing import Iterable, Sequence

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model
from telegram import Bot
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from users.constants import (
    ADMIN_GROUP_NAME,
    COACH_GROUP_NAME,
    MANAGER_GROUP_NAME,
)

logger = logging.getLogger(__name__)

User = get_user_model()


def _clean_ids(raw_ids: Iterable[str]) -> list[str]:
    return [str(chat_id).strip() for chat_id in raw_ids if str(chat_id).strip()]


def _env_ids(setting_name: str, *, fallback: str | None = None) -> list[str]:
    raw_ids: Iterable[str] = getattr(settings, setting_name, []) or []
    cleaned = _clean_ids(raw_ids)
    if not cleaned and fallback:
        cleaned = [str(fallback)]
    return cleaned


def get_admin_env_chat_ids() -> Sequence[str]:
    return _env_ids(
        "TELEGRAM_ADMIN_IDS",
        fallback=getattr(settings, "TELEGRAM_LOG_CHAT_ID", None),
    )


def get_coach_env_chat_ids() -> Sequence[str]:
    env_ids = _env_ids("TELEGRAM_COACH_IDS")
    if env_ids:
        return env_ids
    return get_admin_env_chat_ids()


def get_manager_env_chat_ids() -> Sequence[str]:
    env_ids = _env_ids("TELEGRAM_MANAGER_IDS")
    if env_ids:
        return env_ids
    return get_admin_env_chat_ids()


async def _fetch_group_chat_ids(group_name: str) -> list[int]:
    queryset = (
        User.objects.filter(groups__name=group_name, tg_id__isnull=False)
        .values_list("tg_id", flat=True)
        .distinct()
    )
    return await sync_to_async(list, thread_sensitive=True)(queryset)


async def _resolve_recipient_ids(group_name: str, env_ids: Sequence[str]) -> list[int]:
    recipients: set[int] = set()

    for raw_id in env_ids:
        try:
            recipients.add(int(raw_id))
        except (TypeError, ValueError):
            logger.warning("Некорректный chat_id в настройках %s: %s", group_name, raw_id)

    for chat_id in await _fetch_group_chat_ids(group_name):
        recipients.add(int(chat_id))

    return list(recipients)


async def _user_has_role(tg_id: int | None, group_name: str, env_ids: Sequence[str]) -> bool:
    if not tg_id:
        return False
    if str(tg_id) in env_ids:
        return True
    return await sync_to_async(
        User.objects.filter(tg_id=tg_id, groups__name=group_name).exists,
        thread_sensitive=True,
    )()


async def user_is_admin(tg_id: int | None) -> bool:
    return await _user_has_role(tg_id, ADMIN_GROUP_NAME, get_admin_env_chat_ids())


async def user_is_coach(tg_id: int | None) -> bool:
    return await _user_has_role(tg_id, COACH_GROUP_NAME, get_coach_env_chat_ids())


async def user_is_manager(tg_id: int | None) -> bool:
    return await _user_has_role(tg_id, MANAGER_GROUP_NAME, get_manager_env_chat_ids())


async def notify_admins_context(
    context: ContextTypes.DEFAULT_TYPE,
    message: str,
    reply_markup=None,
) -> None:
    """Send notification using handler's context."""
    await notify_admins_bot(context.bot, message, reply_markup=reply_markup)


async def notify_admins_bot(bot: Bot, message: str, reply_markup=None) -> None:
    """Send notification using bot instance."""
    for chat_id in await _resolve_recipient_ids(ADMIN_GROUP_NAME, get_admin_env_chat_ids()):
        try:
            await bot.send_message(chat_id=chat_id, text=message, reply_markup=reply_markup)
        except TelegramError as exc:
            logger.exception("Не удалось отправить сообщение админу %s: %s", chat_id, exc)


async def notify_coaches_bot(bot: Bot, message: str, reply_markup=None) -> None:
    for chat_id in await _resolve_recipient_ids(COACH_GROUP_NAME, get_coach_env_chat_ids()):
        try:
            await bot.send_message(chat_id=chat_id, text=message, reply_markup=reply_markup)
        except TelegramError as exc:
            logger.exception("Не удалось отправить сообщение тренеру %s: %s", chat_id, exc)


async def notify_managers_bot(bot: Bot, message: str, reply_markup=None) -> None:
    for chat_id in await _resolve_recipient_ids(MANAGER_GROUP_NAME, get_manager_env_chat_ids()):
        try:
            await bot.send_message(chat_id=chat_id, text=message, reply_markup=reply_markup)
        except TelegramError as exc:
            logger.exception("Не удалось отправить сообщение менеджеру %s: %s", chat_id, exc)
