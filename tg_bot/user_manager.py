import logging
from typing import Iterable, Optional

from asgiref.sync import sync_to_async
from django.utils import timezone
from telegram import Chat, ChatMemberUpdated, Message, Update, User
from telegram.constants import ChatMemberStatus, ChatType

from bot.models import TelegramChat, TelegramParticipant

logger = logging.getLogger(__name__)


def _coalesce(value: Optional[str]) -> str:
    return value or ""


def _chat_extra(chat: Chat) -> dict:
    """Collect additional chat metadata."""
    extra: dict = {}
    if chat.permissions:
        extra["permissions"] = chat.permissions.to_dict()
    if chat.location:
        extra["location"] = chat.location.to_dict()
    if chat.has_protected_content is not None:
        extra["has_protected_content"] = chat.has_protected_content
    if chat.linked_chat_id is not None:
        extra["linked_chat_id"] = chat.linked_chat_id
    if chat.bio:
        extra["bio"] = chat.bio
    if chat.photo:
        extra["photo"] = chat.photo.to_dict()
    if chat.active_usernames:
        extra["active_usernames"] = chat.active_usernames
    return extra


def _participant_extra(message: Optional[Message] = None) -> dict:
    """Store lightweight context about participant interaction."""
    if not message:
        return {}
    payload: dict = {
        "message_id": message.message_id,
        "via_bot_id": message.via_bot.id if message.via_bot else None,
        "is_automatic_forward": message.is_automatic_forward,
        "has_protected_content": message.has_protected_content,
    }
    if message.forward_origin:
        payload["forward_origin"] = message.forward_origin.to_dict()
    return payload


def _map_member_status(status: Optional[str]) -> str:
    mapping = {
        ChatMemberStatus.OWNER: TelegramParticipant.MemberStatus.CREATOR,
        ChatMemberStatus.ADMINISTRATOR: TelegramParticipant.MemberStatus.ADMIN,
        ChatMemberStatus.MEMBER: TelegramParticipant.MemberStatus.MEMBER,
        ChatMemberStatus.RESTRICTED: TelegramParticipant.MemberStatus.RESTRICTED,
        ChatMemberStatus.LEFT: TelegramParticipant.MemberStatus.LEFT,
        ChatMemberStatus.KICKED: TelegramParticipant.MemberStatus.KICKED,
    }
    if not status:
        return TelegramParticipant.MemberStatus.UNKNOWN
    return mapping.get(status, TelegramParticipant.MemberStatus.UNKNOWN)


async def _upsert_chat(chat: Chat) -> TelegramChat:
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
        status = TelegramParticipant.MemberStatus.UNKNOWN
        if chat.type in {ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL}:
            status = TelegramParticipant.MemberStatus.MEMBER
        elif chat.type == ChatType.PRIVATE:
            status = TelegramParticipant.MemberStatus.MEMBER
        await _upsert_participant(
            chat_obj,
            message.from_user,
            status=status,
            message=message,
        )

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
