import logging
from typing import Iterable, Optional

from asgiref.sync import sync_to_async
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from tg_bot.services.notifications import (
    get_admin_chat_ids,
    notify_admins_context,
)
from users.models import User

logger = logging.getLogger(__name__)

ADMIN_PAGE_SIZE = 5


def _is_admin(user_id: Optional[int]) -> bool:
    if not user_id:
        return False
    return str(user_id) in get_admin_chat_ids()


async def _ensure_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _is_admin(update.effective_user.id):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="У вас нет прав для выполнения этого действия.",
        )
        return False
    return True


async def _fetch_pending_users(limit: int = ADMIN_PAGE_SIZE) -> list[User]:
    return await sync_to_async(list)(
        User.objects.filter(is_approved=False)
        .order_by("date_joined")[:limit]
    )


def _pending_keyboard(users: Iterable[User]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=user.display_name(),
                callback_data=f"admin:approve:{user.pk}",
            )
        ]
        for user in users
    ]
    rows.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="admin:refresh")])
    return InlineKeyboardMarkup(rows)


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_admin(update, context):
        return

    pending_users = await _fetch_pending_users()
    if not pending_users:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Нет пользователей, ожидающих подтверждения.",
        )
        return

    text_lines = ["Ожидают подтверждения:"]
    for user in pending_users:
        text_lines.append(f"• {user.display_name()} (id={user.pk}, email={user.email or '-'})")

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(text_lines),
        reply_markup=_pending_keyboard(pending_users),
    )


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_admin(update, context):
        return

    if not context.args:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Использование: /approve <tg_id|email> [family_id]",
        )
        return

    identifier = context.args[0]
    user = None
    if identifier.isdigit():
        user = await sync_to_async(User.objects.filter(tg_id=int(identifier)).first)()
    if not user:
        user = await sync_to_async(User.objects.filter(email=identifier).first)()

    if not user:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Пользователь не найден",
        )
        return

    update_fields = ["is_approved"]
    user.is_approved = True

    if len(context.args) > 1:
        family_id = context.args[1]
        from school.models import Family

        family = await sync_to_async(Family.objects.filter(pk=family_id).first)()
        if family:
            user.family = family
            update_fields.append("family")
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Семья не найдена, назначение пропущено",
            )

    await sync_to_async(user.save)(update_fields=update_fields)

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Пользователь {user.display_name()} подтверждён",
    )

    if user.tg_id:
        try:
            await context.bot.send_message(
                chat_id=user.tg_id,
                text="Ваш доступ к системе спортивной школы подтверждён!",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось отправить уведомление пользователю %s: %s", user.tg_id, exc)


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_admin(update, context):
        return

    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if data == "admin:refresh":
        pending_users = await _fetch_pending_users()
        if not pending_users:
            await query.edit_message_text("Нет пользователей, ожидающих подтверждения.")
            return
        text_lines = ["Ожидают подтверждения:"]
        for user in pending_users:
            text_lines.append(f"• {user.display_name()} (id={user.pk}, email={user.email or '-'})")
        await query.edit_message_text(
            "\n".join(text_lines),
            reply_markup=_pending_keyboard(pending_users),
        )
        return

    if not data.startswith("admin:approve:"):
        await query.answer("Неизвестное действие", show_alert=True)
        return

    _, _, user_id_raw = data.partition("admin:approve:")
    try:
        user_id = int(user_id_raw)
    except ValueError:
        await query.answer("Некорректный идентификатор", show_alert=True)
        return

    user = await sync_to_async(User.objects.filter(pk=user_id).first)()
    if not user:
        await query.answer("Пользователь не найден", show_alert=True)
        return

    user.is_approved = True
    await sync_to_async(user.save)(update_fields=["is_approved"])

    await notify_admins_context(
        context,
        f"✅ Пользователь {user.display_name()} подтверждён через меню (id={user.pk})",
    )
    try:
        if user.tg_id:
            await context.bot.send_message(
                chat_id=user.tg_id,
                text="Ваш доступ к системе спортивной школы подтверждён!",
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось отправить уведомление пользователю %s: %s", user.tg_id, exc)

    await query.answer("Пользователь подтверждён", show_alert=False)
    pending_users = await _fetch_pending_users()
    if not pending_users:
        await query.edit_message_text("Все пользователи подтверждены 🎉")
        return
    text_lines = ["Ожидают подтверждения:"]
    for usr in pending_users:
        text_lines.append(f"• {usr.display_name()} (id={usr.pk}, email={usr.email or '-'})")
    await query.edit_message_text(
        "\n".join(text_lines),
        reply_markup=_pending_keyboard(pending_users),
    )
