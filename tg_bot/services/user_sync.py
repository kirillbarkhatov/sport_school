from __future__ import annotations

from asgiref.sync import sync_to_async
from telegram import User as TelegramUser

from users.services import (
    UserTelegramProfile,
    ensure_user_for_start,
    sync_existing_user,
    update_user_comment,
)


async def ensure_user_for_start_async(
    tg_user: TelegramUser,
    token: str | None = None,
):
    profile = UserTelegramProfile.from_telegram(tg_user)
    return await sync_to_async(
        ensure_user_for_start,
        thread_sensitive=True,
    )(profile, token)


async def sync_existing_user_async(tg_user: TelegramUser):
    profile = UserTelegramProfile.from_telegram(tg_user)
    return await sync_to_async(
        sync_existing_user,
        thread_sensitive=True,
    )(profile)


async def update_user_comment_async(user, comment: str):
    return await sync_to_async(
        update_user_comment,
        thread_sensitive=True,
    )(user, comment)
