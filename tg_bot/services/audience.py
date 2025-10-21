import asyncio
import logging
from typing import Iterable, Optional

from asgiref.sync import sync_to_async
from django.utils import timezone
from telegram import Bot, Chat, ChatMemberUpdated, Message, Update, User
from telegram.constants import ChatMemberStatus, ChatType
from telegram.error import TelegramError

from bot.models import TelegramChat, TelegramParticipant
from tg_bot.services.notifications import notify_admins_bot
from tg_bot.services.user_sync import sync_existing_user_async

logger = logging.getLogger(__name__)

__all__ = [
    "track_audience_entry",
    "chat_member_entry",
    "track_audience",
    "handle_chat_member_update",
    "sync_chat_snapshot",
]


def _coalesce(value: Optional[str]) -> str:
    return value or ""


def _chat_extra(chat: Chat) -> dict:
    """Collect additional chat metadata."""
    extra: dict = {}

    permissions = getattr(chat, "permissions", None)
    if permissions:
        extra["permissions"] = permissions.to_dict()

    location = getattr(chat, "location", None)
    if location:
        extra["location"] = location.to_dict()

    has_protected_content = getattr(chat, "has_protected_content", None)
    if has_protected_content is not None:
        extra["has_protected_content"] = has_protected_content

    linked_chat_id = getattr(chat, "linked_chat_id", None)
    if linked_chat_id is not None:
        extra["linked_chat_id"] = linked_chat_id

    bio = getattr(chat, "bio", None)
    if bio:
        extra["bio"] = bio

    photo = getattr(chat, "photo", None)
    if photo:
        extra["photo"] = photo.to_dict()

    active_usernames = getattr(chat, "active_usernames", None)
    if active_usernames:
        extra["active_usernames"] = active_usernames

    available_reactions = getattr(chat, "available_reactions", None)
    if available_reactions:
        extra["available_reactions"] = available_reactions

    return extra


def _participant_extra(message: Optional[Message] = None) -> dict:
    """Store lightweight context about participant interaction."""
    if not message:
        return {}
    payload: dict = {
        "message_id": message.message_id,
        "via_bot_id": message.via_bot.id if message.via_bot else None,
        "is_automatic_forward": getattr(message, "is_automatic_forward", None),
        "has_protected_content": getattr(message, "has_protected_content", None),
    }
    if message.forward_origin:
        payload["forward_origin"] = message.forward_origin.to_dict()
    return payload


def _map_member_status(status: Optional[str]) -> str:
    if not status:
        return TelegramParticipant.MemberStatus.UNKNOWN

    mapping = {
        ChatMemberStatus.OWNER: TelegramParticipant.MemberStatus.CREATOR,
        ChatMemberStatus.ADMINISTRATOR: TelegramParticipant.MemberStatus.ADMIN,
        ChatMemberStatus.MEMBER: TelegramParticipant.MemberStatus.MEMBER,
        ChatMemberStatus.RESTRICTED: TelegramParticipant.MemberStatus.RESTRICTED,
        ChatMemberStatus.LEFT: TelegramParticipant.MemberStatus.LEFT,
        ChatMemberStatus.BANNED: TelegramParticipant.MemberStatus.KICKED,
        "kicked": TelegramParticipant.MemberStatus.KICKED,  # compat with legacy PTB
    }
    return mapping.get(status, TelegramParticipant.MemberStatus.UNKNOWN)


async def _upsert_chat(chat: Chat, *, member_count: Optional[int] = None) -> TelegramChat:
    now = timezone.now()
    defaults = {
        "type": chat.type or TelegramChat.ChatType.UNKNOWN,
        "title": _coalesce(chat.title),
        "username": _coalesce(chat.username),
        "description": _coalesce(getattr(chat, "description", "")),
        "invite_link": _coalesce(getattr(chat, "invite_link", "")),
        "last_seen": now,
    }
    extra = _chat_extra(chat)
    if member_count is not None:
        extra.setdefault("stats", {})["member_count"] = member_count
    if extra:
        defaults["extra_data"] = extra
    chat_obj, _ = await sync_to_async(
        TelegramChat.objects.update_or_create,
        thread_sensitive=True,
    )(chat_id=chat.id, defaults=defaults)
    return chat_obj


async def _upsert_participant(
    chat_obj: TelegramChat,
    user: User,
    *,
    status: Optional[str] = None,
    custom_title: Optional[str] = None,
    message: Optional[Message] = None,
    extra: Optional[dict] = None,
) -> None:
    now = timezone.now()
    defaults = {
        "is_bot": bool(user.is_bot),
        "first_name": _coalesce(user.first_name),
        "last_name": _coalesce(user.last_name),
        "username": _coalesce(user.username),
        "language_code": _coalesce(getattr(user, "language_code", "")),
        "last_seen": now,
    }
    if status:
        defaults["status"] = status
    if custom_title is not None:
        defaults["custom_title"] = custom_title or ""
    aggregated_extra = extra or {}
    context_extra = _participant_extra(message)
    if context_extra:
        aggregated_extra.update(context_extra)
    if aggregated_extra:
        defaults["extra_data"] = aggregated_extra

    await sync_to_async(
        TelegramParticipant.objects.update_or_create,
        thread_sensitive=True,
    )(
        chat=chat_obj,
        user_id=user.id,
        defaults=defaults,
    )


async def _register_user_interaction(message: Message) -> None:
    chat = message.chat
    chat_obj = await _upsert_chat(chat)

    if message.from_user:
        status = TelegramParticipant.MemberStatus.MEMBER
        if chat.type not in {ChatType.PRIVATE, ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL}:
            status = TelegramParticipant.MemberStatus.UNKNOWN
        await _upsert_participant(
            chat_obj,
            message.from_user,
            status=status,
            message=message,
        )
        await sync_existing_user_async(message.from_user)

    if message.new_chat_members:
        for member in message.new_chat_members:
            await _upsert_participant(
                chat_obj,
                member,
                status=TelegramParticipant.MemberStatus.MEMBER,
                message=message,
            )

    if message.left_chat_member:
        await _upsert_participant(
            chat_obj,
            message.left_chat_member,
            status=TelegramParticipant.MemberStatus.LEFT,
            message=message,
        )


async def _register_sender_chat(message: Message) -> None:
    """Store info about anonymous admin / channel sender chat."""
    if not message.sender_chat:
        return
    sender_chat = message.sender_chat
    # When sender chat equals message chat, it is already processed.
    if sender_chat.id == message.chat.id:
        return
    await _upsert_chat(sender_chat)


async def track_audience(update: Update, _: object) -> None:
    """Gather chat/users activity from any update."""
    try:
        messages: Iterable[Optional[Message]] = (
            update.message,
            update.edited_message,
            update.channel_post,
            update.edited_channel_post,
        )
        for message in messages:
            if message:
                await _register_user_interaction(message)
                await _register_sender_chat(message)

        if update.callback_query:
            callback = update.callback_query
            if callback.message:
                await _register_user_interaction(callback.message)
            if callback.from_user and callback.message:
                chat_obj = await _upsert_chat(callback.message.chat)
                await _upsert_participant(
                    chat_obj,
                    callback.from_user,
                    status=TelegramParticipant.MemberStatus.MEMBER,
                )

        if update.inline_query and update.inline_query.from_user:
            # Inline queries happen outside a chat context; treat as private chat.
            pseudo_chat = Chat(
                id=update.inline_query.from_user.id,
                type=ChatType.PRIVATE,
                first_name=update.inline_query.from_user.first_name,
                last_name=update.inline_query.from_user.last_name,
                username=update.inline_query.from_user.username,
            )
            chat_obj = await _upsert_chat(pseudo_chat)
            await _upsert_participant(
                chat_obj,
                update.inline_query.from_user,
                status=TelegramParticipant.MemberStatus.MEMBER,
            )
    except Exception:
        logger.exception("Не удалось сохранить сведения о собеседниках бота")


async def handle_chat_member_update(update: Update, _: object) -> None:
    """Persist changes in chat membership (group/channel)."""
    event: Optional[ChatMemberUpdated] = update.chat_member or update.my_chat_member
    if not event:
        return
    try:
        chat_obj = await _upsert_chat(event.chat)

        if event.from_user:
            await _upsert_participant(chat_obj, event.from_user)

        member = event.new_chat_member
        if not member:
            return

        status = _map_member_status(member.status)
        extra = member.to_dict()
        custom_title = getattr(member, "custom_title", None)

        await _upsert_participant(
            chat_obj,
            member.user,
            status=status,
            custom_title=custom_title,
            extra=extra,
        )
    except Exception:
        logger.exception("Не удалось обновить статус участника чата")


async def sync_chat_snapshot(bot: Bot, chat_id: int) -> TelegramChat:
    """Explicitly fetch chat info and administrators for the given chat ID."""
    await notify_admins_bot(bot, f"🔍 Запрашиваю данные чата {chat_id}")
    chat = await bot.get_chat(chat_id)
    member_count = None
    try:
        member_count = await bot.get_chat_member_count(chat_id)
        await notify_admins_bot(
            bot, f"📊 Количество участников в чате {chat_id}: {member_count}"
        )
    except TelegramError as exc:
        logger.info("Не удалось получить количество участников %s: %s", chat_id, exc)
    await notify_admins_bot(bot, f"ℹ️ Информация о чате {chat_id}:\n{chat.to_dict()}")
    chat_obj = await _upsert_chat(chat, member_count=member_count)
    await notify_admins_bot(
        bot,
        "💾 Сохранён чат в БД: "
        f"id={chat_obj.pk}, chat_id={chat_obj.chat_id}, type={chat_obj.type}, title={chat_obj.title}",
    )

    try:
        administrators = await bot.get_chat_administrators(chat_id)
        await notify_admins_bot(
            bot, f"👥 Получено администраторов: {len(administrators)} для чата {chat_id}"
        )
    except TelegramError as exc:
        logger.warning("Не удалось получить администраторов чата %s: %s", chat_id, exc)
        await notify_admins_bot(
            bot, f"⚠️ Не удалось получить администраторов чата {chat_id}: {exc}"
        )
        return chat_obj

    for member in administrators:
        custom_title = getattr(member, "custom_title", None)
        extra = member.to_dict()
        await notify_admins_bot(
            bot,
            "👤 Сохраняю администратора:\n"
            f"user_id={member.user.id}\n"
            f"username={member.user.username}\n"
            f"status={member.status}\n"
            f"custom_title={custom_title}",
        )
        await _upsert_participant(
            chat_obj,
            member.user,
            status=_map_member_status(member.status),
            custom_title=custom_title,
            extra=extra,
        )

    await notify_admins_bot(
        bot, f"✅ Синхронизация завершена для чата {chat_id} (БД id={chat_obj.pk})"
    )
    return chat_obj


async def track_audience_entry(update: Update, context) -> None:
    async def runner() -> None:
        await track_audience(update, context)

    app = getattr(context, "application", None)
    if app:
        app.create_task(runner(), name="track_audience")
    else:
        asyncio.create_task(runner(), name="track_audience")


async def chat_member_entry(update: Update, context) -> None:
    async def runner() -> None:
        await handle_chat_member_update(update, context)

    app = getattr(context, "application", None)
    if app:
        app.create_task(runner(), name="chat_member_update")
    else:
        asyncio.create_task(runner(), name="chat_member_update")
