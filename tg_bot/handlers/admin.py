import logging
from typing import Iterable, Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from tg_bot.handlers.auth import build_authenticated_keyboard, _format_login_instructions
from tg_bot.services.notifications import (
    notify_admins_context,
    user_is_admin,
    user_is_manager,
)
from school.models import Person
from users.models import User, UserPersonLink, UserPersonLinkStatus

logger = logging.getLogger(__name__)

ADMIN_PAGE_SIZE = 5
LINK_DECISION_FIELDS = ["status", "decided_by", "decided_at", "decision_note", "updated_at"]


async def _ensure_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not await user_is_admin(update.effective_user.id):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="У вас нет прав для выполнения этого действия.",
        )
        return False
    return True


async def _ensure_approver(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    tg_id = update.effective_user.id if update.effective_user else None
    if await user_is_admin(tg_id) or await user_is_manager(tg_id):
        return True
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="У вас нет прав для выполнения этого действия.",
    )
    return False


async def _notify_user_approved(bot, user: User) -> None:
    if not user.tg_id:
        return
    message = "\n\n".join(
        [
            "🎉 Ваша заявка подтверждена!",
            _format_login_instructions(None),
            "Доступные действия:",
        ]
    )
    try:
        await bot.send_message(
            chat_id=user.tg_id,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=build_authenticated_keyboard(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось отправить сообщение пользователю %s: %s", user.tg_id, exc)


async def _fetch_pending_users(limit: int = ADMIN_PAGE_SIZE) -> list[User]:
    queryset = (
        User.objects.select_related("link", "link__suggested_person")
        .filter(link__status=UserPersonLinkStatus.PENDING)
        .order_by("date_joined")
    )
    return await sync_to_async(list, thread_sensitive=True)(queryset[:limit])


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


async def _get_admin_user(tg_id: Optional[int]) -> Optional[User]:
    if not tg_id:
        return None
    return await sync_to_async(
        User.objects.filter(tg_id=tg_id).first,
        thread_sensitive=True,
    )()


def _confirm_suggested_link_sync(user_id: int, admin_user_id: Optional[int]) -> tuple[User, UserPersonLink]:
    admin_user = None
    if admin_user_id:
        admin_user = User.objects.filter(pk=admin_user_id).first()
    user = User.objects.select_related("link", "link__suggested_person").get(pk=user_id)
    link = user.link
    if not link or not link.suggested_person_id:
        raise ValueError("Для этого пользователя нет предложенной персоны")

    link.apply_decision(UserPersonLinkStatus.APPROVED, decided_by=admin_user)
    link.save(update_fields=LINK_DECISION_FIELDS)

    user.person_id = link.suggested_person_id
    user.is_approved = True
    user.save(update_fields=["person", "is_approved"])
    return user, link


def _set_person_link_sync(user_id: int, person_id: int, admin_user_id: Optional[int]) -> tuple[User, UserPersonLink]:
    admin_user = None
    if admin_user_id:
        admin_user = User.objects.filter(pk=admin_user_id).first()
    user = User.objects.select_related("link").get(pk=user_id)
    person = Person.objects.get(pk=person_id)
    link, _ = UserPersonLink.objects.get_or_create(user=user)
    link.suggested_person = person
    reasons = set(link.matched_reasons or [])
    reasons.add("manual")
    link.matched_reasons = sorted(reasons)
    link.apply_decision(UserPersonLinkStatus.APPROVED, decided_by=admin_user)
    link.save(update_fields=["suggested_person", "matched_reasons", *LINK_DECISION_FIELDS])

    user.person = person
    user.is_approved = True
    user.save(update_fields=["person", "is_approved"])
    return user, link


def _reject_link_sync(user_id: int, admin_user_id: Optional[int]) -> tuple[User, UserPersonLink]:
    admin_user = None
    if admin_user_id:
        admin_user = User.objects.filter(pk=admin_user_id).first()
    user = User.objects.select_related("link").get(pk=user_id)
    link, _ = UserPersonLink.objects.get_or_create(user=user)
    link.apply_decision(UserPersonLinkStatus.REJECTED, decided_by=admin_user)
    link.save(update_fields=LINK_DECISION_FIELDS)

    user.person = None
    user.is_approved = False
    user.save(update_fields=["person", "is_approved"])
    return user, link


def _list_person_candidates_sync(user: User) -> list[Person]:
    surname = (user.last_name or user.tg_last_name or user.first_name or "").strip()
    queryset = Person.objects.order_by("surname", "name")
    if surname:
        queryset = queryset.filter(surname__icontains=surname)
    return list(queryset[:10])


async def _confirm_suggested_link(user_id: int, admin_user_id: Optional[int]) -> tuple[User, UserPersonLink]:
    return await sync_to_async(
        _confirm_suggested_link_sync,
        thread_sensitive=True,
    )(user_id, admin_user_id)


async def _set_person_link(user_id: int, person_id: int, admin_user_id: Optional[int]) -> tuple[User, UserPersonLink]:
    return await sync_to_async(
        _set_person_link_sync,
        thread_sensitive=True,
    )(user_id, person_id, admin_user_id)


async def _reject_link(user_id: int, admin_user_id: Optional[int]) -> tuple[User, UserPersonLink]:
    return await sync_to_async(
        _reject_link_sync,
        thread_sensitive=True,
    )(user_id, admin_user_id)


async def _person_candidates_keyboard(user: User) -> InlineKeyboardMarkup:
    persons = await sync_to_async(_list_person_candidates_sync, thread_sensitive=True)(user)
    if not persons:
        rows = [[InlineKeyboardButton("Нет подходящих вариантов", callback_data="admin:noop")]]
    else:
        rows = [
            [
                InlineKeyboardButton(
                    text=f"{person.surname} {person.name} ({person.pk})",
                    callback_data=f"admin_link:set_person:{user.pk}:{person.pk}",
                )
            ]
            for person in persons
        ]
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="admin:refresh")])
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
        link = getattr(user, "link", None)
        text_lines.append(
            f"• {user.display_name()} (id={user.pk}, tg=@{user.tg_username or '-'}, телефон={user.phone or '-'})"
        )
        if link and link.suggested_person_id:
            suggested = link.suggested_person
            text_lines.append(
                f"  Предложение: {suggested.surname} {suggested.name}"
            )
        if link and link.user_comment:
            text_lines.append(f"  Комментарий: {link.user_comment}")

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(text_lines),
        reply_markup=_pending_keyboard(pending_users),
    )


async def _update_pending_overview(message) -> None:
    pending_users = await _fetch_pending_users()
    if not pending_users:
        await message.edit_text("Все пользователи подтверждены 🎉")
        return

    text_lines = ["Ожидают подтверждения:"]
    for user in pending_users:
        link = getattr(user, "link", None)
        text_lines.append(
            f"• {user.display_name()} (id={user.pk}, tg=@{user.tg_username or '-'}, телефон={user.phone or '-'})"
        )
        if link and link.suggested_person_id:
            suggested = link.suggested_person
            text_lines.append(
                f"  Предложение: {suggested.surname} {suggested.name}"
            )
        if link and link.user_comment:
            text_lines.append(f"  Комментарий: {link.user_comment}")

    try:
        await message.edit_text(
            "\n".join(text_lines),
            reply_markup=_pending_keyboard(pending_users),
        )
    except BadRequest as exc:
        if "message is not modified" in str(exc).lower():
            logger.debug("Пропуск обновления списка: сообщение не изменилось")
            return
        raise


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_admin(update, context):
        return

    if not context.args:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Использование: /approve <tg_id|email> [person_id]",
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

    admin_user = await _get_admin_user(update.effective_user.id)
    admin_user_id = admin_user.pk if admin_user else None

    try:
        if len(context.args) > 1:
            person_id = int(context.args[1])
            user, link = await _set_person_link(user.pk, person_id, admin_user_id)
            action_text = f"Связь установлена с персоной #{person_id}"
        else:
            user, link = await _confirm_suggested_link(user.pk, admin_user_id)
            action_text = "Предложенная связь подтверждена"
    except ValueError as exc:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=str(exc),
        )
        return
    except Person.DoesNotExist:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Персона не найдена",
        )
        return

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Пользователь {user.display_name()} подтверждён. {action_text}.",
    )

    await notify_admins_context(
        context,
        f"✅ {action_text} для пользователя {user.display_name()} (id={user.pk})",
    )
    await _notify_user_approved(context.bot, user)


async def handle_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data if query else None
    chat_id = update.effective_chat.id if update.effective_chat else None
    logger.debug(
        "handle_admin_callback received callback: data=%s chat_id=%s user_id=%s",
        data,
        chat_id,
        update.effective_user.id if update.effective_user else None,
    )
    if not await _ensure_approver(update, context):
        return

    data = data or ""
    if data == "admin:noop":
        await query.answer()
        return

    await query.answer()

    if data == "admin:refresh":
        await _update_pending_overview(query.message)
        return

    if data.startswith("admin_link:"):
        parts = data.split(":")
        if len(parts) < 3:
            await query.answer("Некорректное действие", show_alert=True)
            return
        action = parts[1]
        try:
            user_id = int(parts[2])
        except ValueError:
            await query.answer("Некорректный идентификатор", show_alert=True)
            return

        admin_user = await _get_admin_user(update.effective_user.id)
        admin_user_id = admin_user.pk if admin_user else None

        if action == "confirm":
            try:
                user, link = await _confirm_suggested_link(user_id, admin_user_id)
            except ValueError as exc:
                await query.answer(str(exc), show_alert=True)
                return
            await query.answer("Связь подтверждена", show_alert=False)
            await notify_admins_context(
                context,
                f"✅ Связь пользователя {user.display_name()} подтверждена",
            )
            await _notify_user_approved(context.bot, user)
            await _update_pending_overview(query.message)
            return

        if action == "select":
            user_obj = await sync_to_async(
                User.objects.select_related("link").get,
                thread_sensitive=True,
            )(pk=user_id)
            keyboard = await _person_candidates_keyboard(user_obj)
            await query.message.reply_text(
                f"Выберите членa клуба для {user_obj.display_name()} (id={user_obj.pk}):",
                reply_markup=keyboard,
            )
            return

        if action == "set_person":
            if len(parts) < 4:
                await query.answer("Не указан идентификатор персоны", show_alert=True)
                return
            try:
                person_id = int(parts[3])
            except ValueError:
                await query.answer("Некорректный идентификатор персоны", show_alert=True)
                return
            try:
                user, link = await _set_person_link(user_id, person_id, admin_user_id)
            except Person.DoesNotExist:
                await query.answer("Персона не найдена", show_alert=True)
                return
            await query.message.reply_text(
                f"Связь пользователя {user.display_name()} установлена с {link.suggested_person}.",
            )
            await notify_admins_context(
                context,
                f"✅ Установлена связь пользователя {user.display_name()} с персоной {link.suggested_person}",
            )
            await _notify_user_approved(context.bot, user)
            await _update_pending_overview(query.message)
            return

        if action == "reject":
            user, link = await _reject_link(user_id, admin_user_id)
            await query.message.reply_text(
                f"Заявка пользователя {user.display_name()} отклонена.",
            )
            await notify_admins_context(
                context,
                f"🚫 Заявка пользователя {user.display_name()} отклонена",
            )
            await _update_pending_overview(query.message)
            return

        if action == "create":
            url = settings.SITE_BASE_URL.rstrip("/") + "/members/person/create/"
            await query.message.reply_text(
                "Создайте нового члена клуба в административном интерфейсе: "
                f"{url}. После создания вернитесь и выберите его в списке.",
            )
            return

        await query.answer("Неизвестное действие", show_alert=True)
        return

    if data.startswith("admin:approve:"):
        _, _, user_id_raw = data.partition("admin:approve:")
        try:
            user_id = int(user_id_raw)
        except ValueError:
            await query.answer("Некорректный идентификатор", show_alert=True)
            return

        admin_user = await _get_admin_user(update.effective_user.id)
        admin_user_id = admin_user.pk if admin_user else None

        try:
            user, link = await _confirm_suggested_link(user_id, admin_user_id)
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)
            return

        await notify_admins_context(
            context,
            f"✅ Пользователь {user.display_name()} подтверждён через меню (id={user.pk})",
        )
        await _notify_user_approved(context.bot, user)
        await _update_pending_overview(query.message)
        return

    await query.answer("Неизвестное действие", show_alert=True)
