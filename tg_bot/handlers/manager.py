from __future__ import annotations

import logging
from datetime import datetime, timedelta

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from school.choices import TrainingKind, TrainingLocation
from school.models import Athlete, Class, ClassEnrollment, Family, FamilyMember, Group
from tg_bot.handlers.admin import build_pending_overview_payload
from tg_bot.services.notifications import user_is_admin, user_is_manager
from tg_bot.services.training_overview import (
    ATTENDANCE_STATUS_ICONS,
    COACH_STATUS_HINTS,
    get_training_summary,
    get_upcoming_trainings_for_user,
    update_attendance_status,
)
from users.models import User, UserPersonLinkStatus

logger = logging.getLogger(__name__)

MANAGER_CALLBACK_PREFIX = "manager"
MANAGER_STATE_KEY = "manager_state"
MAX_SEARCH_RESULTS = 12
TRAINING_SCOPE_DEFAULT_LIMIT = 8


def _build_manager_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("👪 Семьи", callback_data=f"{MANAGER_CALLBACK_PREFIX}:families")],
            [InlineKeyboardButton("🧑 Пользователи", callback_data=f"{MANAGER_CALLBACK_PREFIX}:users")],
            [InlineKeyboardButton("📋 Заявки на подтверждение", callback_data=f"{MANAGER_CALLBACK_PREFIX}:approvals")],
            [InlineKeyboardButton("🏃 Перевести спортсмена", callback_data=f"{MANAGER_CALLBACK_PREFIX}:transfer")],
            [InlineKeyboardButton("📅 Тренировки и записи", callback_data=f"{MANAGER_CALLBACK_PREFIX}:trainings")],
        ]
    )


def _set_manager_state(context: ContextTypes.DEFAULT_TYPE, state: dict | None) -> None:
    if state:
        context.user_data[MANAGER_STATE_KEY] = state
    else:
        context.user_data.pop(MANAGER_STATE_KEY, None)


def _get_manager_state(context: ContextTypes.DEFAULT_TYPE) -> dict | None:
    return context.user_data.get(MANAGER_STATE_KEY)


async def _ensure_manager_access(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    tg_id = update.effective_user.id if update.effective_user else None
    if not tg_id:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Не удалось определить пользователя.",
        )
        return False

    if await user_is_manager(tg_id) or await user_is_admin(tg_id):
        return True

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="У вас нет прав менеджера для выполнения этого действия.",
    )
    return False


async def manager_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_manager_access(update, context):
        return

    _set_manager_state(context, None)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Панель менеджера. Выберите раздел:",
        reply_markup=_build_manager_menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# Помощники для работы с семьями
# ---------------------------------------------------------------------------

async def _search_families(query: str, limit: int = MAX_SEARCH_RESULTS) -> list[Family]:
    normalized = (query or "").strip()

    def fetch():
        families = (
            Family.objects.select_related("contact_person")
            .prefetch_related("members__person__athlete")
            .order_by("family_name", "id")
        )
        if not normalized:
            return list(families[:limit])

        filters = Q(family_name__icontains=normalized) | Q(contact_person__surname__icontains=normalized)
        filters |= Q(contact_person__name__icontains=normalized) | Q(members__person__surname__icontains=normalized)
        if normalized.isdigit():
            filters |= Q(id=int(normalized))
        return list(families.filter(filters).distinct()[:limit])

    return await sync_to_async(fetch, thread_sensitive=True)()


def _format_family_header(family: Family) -> str:
    parts = [family.family_name or "Семья без названия"]
    if family.status:
        parts.append(f"Статус: {family.get_status_display()}")
    return "\n".join(parts)


def _format_family_members(family: Family) -> list[str]:
    lines = []
    members: list[FamilyMember] = list(family.members.all())
    if not members:
        return ["Участников не найдено."]
    for member in members:
        person = member.person
        if not person:
            lines.append(f"• {member.get_relation_display()}: (удалённый участник)")
            continue
        person_parts = [person.surname, person.name]
        title = " ".join(part for part in person_parts if part) or "Без имени"
        relation = member.get_relation_display()
        extras: list[str] = []
        if person.phone:
            extras.append(person.phone)
        if relation:
            extras.append(relation.lower())
        athlete = getattr(person, "athlete", None)
        if athlete:
            extras.append("спортсмен")
            if athlete.level:
                extras.append(f"сезон {athlete.level}")
        detail = f"{title} ({', '.join(extras)})" if extras else title
        lines.append(f"• {detail}")
    return lines


async def _prompt_family_search(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit: bool = False, message=None) -> None:
    _set_manager_state(context, {"mode": "family_search"})
    text = (
        "Введите фамилию семьи, имя контактного лица или фамилию участника.\n"
        "Можно указать номер семьи."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")],
        ]
    )
    if edit and message:
        await message.edit_text(text, reply_markup=keyboard)
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=keyboard,
        )


async def _send_family_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str) -> None:
    families = await _search_families(query)
    if not families:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Не удалось найти подходящие семьи. Попробуйте другой запрос.",
        )
        return

    lines = ["Найденные семьи:"]
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for family in families:
        lines.append(f"• {family.family_name or 'Семья без названия'}")
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    family.family_name or f"Семья #{family.id}",
                    callback_data=f"{MANAGER_CALLBACK_PREFIX}:family:{family.id}",
                )
            ]
        )

    keyboard_rows.append(
        [InlineKeyboardButton("🔍 Искать снова", callback_data=f"{MANAGER_CALLBACK_PREFIX}:families")]
    )
    keyboard_rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
    )


async def _load_family(family_id: int) -> Family | None:
    def fetch():
        return (
            Family.objects.select_related("contact_person")
            .prefetch_related("members__person__athlete")
            .filter(id=family_id)
            .first()
        )

    return await sync_to_async(fetch, thread_sensitive=True)()


def _build_family_detail_keyboard(family: Family) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    base_url = settings.SITE_BASE_URL.rstrip("/")
    try:
        family_url = base_url + reverse("members:family_update", args=[family.id])
        rows.append([InlineKeyboardButton("🔗 Открыть семью на сайте", url=family_url)])
    except Exception as exc:  # noqa: BLE001
        logger.debug("Не удалось построить ссылку на семью %s: %s", family.id, exc)

    athletes = []
    for member in family.members.all():
        if member.person and hasattr(member.person, "athlete"):
            athletes.append(member.person.athlete)

    if athletes:
        rows.append(
            [
                InlineKeyboardButton(
                    "🏃 Спортсмены семьи",
                    callback_data=f"{MANAGER_CALLBACK_PREFIX}:transfer_family:{family.id}",
                )
            ]
        )

    rows.append([InlineKeyboardButton("🔍 Найти другую семью", callback_data=f"{MANAGER_CALLBACK_PREFIX}:families")])
    rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])
    return InlineKeyboardMarkup(rows)


async def _send_family_details(update: Update, context: ContextTypes.DEFAULT_TYPE, family_id: int) -> None:
    family = await _load_family(family_id)
    if not family:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Семья не найдена или была удалена.",
        )
        return

    _set_manager_state(context, None)
    lines = [_format_family_header(family)]
    if family.contact_person:
        contact = family.contact_person
        contact_parts = [contact.surname, contact.name, contact.middlename]
        contact_title = " ".join(part for part in contact_parts if part) or "Без имени"
        lines.append("")
        lines.append(f"Контакт: {contact_title}")
        if contact.phone:
            lines.append(f"Телефон: {contact.phone}")
        if contact.email:
            lines.append(f"Email: {contact.email}")
    if family.comment:
        lines.append("")
        lines.append(f"Комментарий: {family.comment}")

    lines.append("")
    lines.append("Участники семьи:")
    lines.extend(_format_family_members(family))

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(lines),
        reply_markup=_build_family_detail_keyboard(family),
    )


# ---------------------------------------------------------------------------
# Работа с пользователями
# ---------------------------------------------------------------------------

async def _search_users(query: str, limit: int = MAX_SEARCH_RESULTS) -> list[User]:
    normalized = (query or "").strip()

    def fetch():
        users = (
            User.objects.select_related("person", "link", "person__athlete")
            .order_by("last_name", "first_name")
        )
        if not normalized:
            return list(users[:limit])

        filters = (
            Q(first_name__icontains=normalized)
            | Q(last_name__icontains=normalized)
            | Q(email__icontains=normalized)
            | Q(phone__icontains=normalized)
            | Q(tg_username__icontains=normalized)
            | Q(person__surname__icontains=normalized)
        )
        if normalized.isdigit():
            filters |= Q(id=int(normalized)) | Q(tg_id=int(normalized))
        return list(users.filter(filters).distinct()[:limit])

    return await sync_to_async(fetch, thread_sensitive=True)()


def _format_user_details(user: User) -> str:
    lines = [user.display_name()]
    contact_parts: list[str] = []
    if user.tg_username:
        contact_parts.append(f"@{user.tg_username}")
    if user.phone:
        contact_parts.append(user.phone)
    if contact_parts:
        lines.append(f"📞 Контакты: {', '.join(contact_parts)}")
    if user.email:
        lines.append(f"📧 Email: {user.email}")

    link = getattr(user, "link", None)
    if link:
        status_emoji = {
            UserPersonLinkStatus.APPROVED: "✅",
            UserPersonLinkStatus.PENDING: "⏳",
            UserPersonLinkStatus.REJECTED: "🚫",
        }.get(link.status, "ℹ️")
        lines.append(f"{status_emoji} Статус подтверждения: {link.get_status_display()}")
        if link.suggested_person_id and link.suggested_person:
            lines.append(f"👥 Связан с: {link.suggested_person}")
        if link.user_comment:
            lines.append(f"💬 Комментарий пользователя: {link.user_comment}")
    elif user.is_approved:
        lines.append("✅ Статус подтверждения: подтверждён вручную.")
    else:
        lines.append("⏳ Статус подтверждения: не подтверждён.")

    if user.person:
        person = user.person
        lines.append("")
        lines.append("Персона:")
        parts = [person.surname, person.name, person.middlename]
        lines.append(f"• {' '.join(p for p in parts if p) or 'Без имени'}")
        contact_details: list[str] = []
        if person.phone:
            contact_details.append(f"📞 {person.phone}")
        if person.email:
            contact_details.append(f"📧 {person.email}")
        for detail in contact_details:
            lines.append(f"  {detail}")
        athlete = getattr(person, "athlete", None)
        if athlete:
            lines.append("  Спортсмен:")
            level_display = athlete.get_level_display() if hasattr(athlete, "get_level_display") else athlete.level
            if level_display:
                lines.append(f"    • Сезон: {level_display}")
            if athlete.rank:
                lines.append(f"    • Разряд: {athlete.rank}")
    return "\n".join(lines)


def _build_user_keyboard(user: User) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    base_url = settings.SITE_BASE_URL.rstrip("/")

    try:
        admin_url = base_url + reverse("admin:users_user_change", args=[user.id])
        rows.append([InlineKeyboardButton("🔗 Пользователь в админке", url=admin_url)])
    except Exception as exc:  # noqa: BLE001
        logger.debug("Не удалось построить ссылку на пользователя %s: %s", user.id, exc)

    if user.person_id:
        try:
            person_url = base_url + reverse("members:members_update", args=[user.person_id])
            rows.append([InlineKeyboardButton("🧾 Карточка персоны", url=person_url)])
        except Exception as exc:  # noqa: BLE001
            logger.debug("Не удалось построить ссылку на персону %s: %s", user.person_id, exc)

    rows.append([InlineKeyboardButton("🔍 Найти другого пользователя", callback_data=f"{MANAGER_CALLBACK_PREFIX}:users")])
    rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])
    return InlineKeyboardMarkup(rows)


async def _prompt_user_search(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit: bool = False, message=None) -> None:
    _set_manager_state(context, {"mode": "user_search"})
    text = (
        "Введите имя, фамилию, email или телефон пользователя.\n"
        "Можно указать telegram @username для поиска."
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")],
        ]
    )
    if edit and message:
        await message.edit_text(text, reply_markup=keyboard)
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=keyboard,
        )


async def _send_user_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str) -> None:
    users = await _search_users(query)
    if not users:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Не удалось найти пользователей по заданным критериям.",
        )
        return

    lines = ["Найденные пользователи:"]
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for user in users:
        contact_parts: list[str] = []
        if user.tg_username:
            contact_parts.append(f"@{user.tg_username}")
        if user.phone:
            contact_parts.append(user.phone)
        contact_text = f" — {', '.join(contact_parts)}" if contact_parts else ""
        lines.append(f"• {user.display_name()}{contact_text}")
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    user.display_name(),
                    callback_data=f"{MANAGER_CALLBACK_PREFIX}:user:{user.id}",
                )
            ]
        )

    keyboard_rows.append([InlineKeyboardButton("🔍 Искать снова", callback_data=f"{MANAGER_CALLBACK_PREFIX}:users")])
    keyboard_rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
    )


async def _load_user_by_id(user_id: int) -> User | None:
    return await sync_to_async(
        User.objects.select_related("person", "link", "person__athlete").filter(id=user_id).first,
        thread_sensitive=True,
    )()


async def _send_user_details(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    user = await _load_user_by_id(user_id)
    if not user:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Пользователь не найден или был удалён.",
        )
        return

    _set_manager_state(context, None)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=_format_user_details(user),
        reply_markup=_build_user_keyboard(user),
    )


# ---------------------------------------------------------------------------
# Подтверждение заявок
# ---------------------------------------------------------------------------

async def _send_pending_overview(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    overview_text, keyboard = await build_pending_overview_payload(include_ids=False)
    if not overview_text:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Все заявки подтверждены 🎉",
        )
        return

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=overview_text,
        reply_markup=keyboard,
    )


# ---------------------------------------------------------------------------
# Переводы спортсменов между группами
# ---------------------------------------------------------------------------

async def _search_athletes(query: str, limit: int = MAX_SEARCH_RESULTS) -> list[Athlete]:
    normalized = (query or "").strip()

    def fetch():
        athletes = (
            Athlete.objects.select_related("person")
            .prefetch_related("groups")
            .order_by("person__surname", "person__name")
        )
        if not normalized:
            return list(athletes[:limit])

        filters = Q(person__surname__icontains=normalized) | Q(person__name__icontains=normalized)
        if normalized.isdigit():
            filters |= Q(id=int(normalized)) | Q(person_id=int(normalized))
        return list(athletes.filter(filters)[:limit])

    return await sync_to_async(fetch, thread_sensitive=True)()


async def _load_athlete(athlete_id: int) -> Athlete | None:
    def fetch():
        return (
            Athlete.objects.select_related("person")
            .prefetch_related("groups")
            .filter(id=athlete_id)
            .first()
        )

    return await sync_to_async(fetch, thread_sensitive=True)()


def _format_athlete_overview(athlete: Athlete) -> str:
    person = athlete.person
    parts = [person.surname, person.name, person.middlename]
    title = " ".join(part for part in parts if part) or "Без имени"
    lines = [title]
    contact_parts: list[str] = []
    if person.phone:
        contact_parts.append(person.phone)
    if person.email:
        contact_parts.append(person.email)
    if contact_parts:
        lines.append(f"Контакты: {', '.join(contact_parts)}")
    if athlete.level:
        level_display = athlete.get_level_display() if hasattr(athlete, "get_level_display") else athlete.level
        lines.append(f"Сезон старта: {level_display}")
    if athlete.rank:
        lines.append(f"Разряд: {athlete.rank}")
    groups = list(athlete.groups.all())
    lines.append("")
    if groups:
        lines.append("Состоит в группах:")
        for group in groups:
            lines.append(f"• {group.name}")
    else:
        lines.append("Не состоит ни в одной группе.")
    return "\n".join(lines)


def _build_athlete_keyboard(athlete: Athlete) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                "➕ Добавить в группу",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:transfer_add:{athlete.id}",
            )
        ]
    ]

    if athlete.groups.exists():
        rows.append(
            [
                InlineKeyboardButton(
                    "➖ Удалить из группы",
                    callback_data=f"{MANAGER_CALLBACK_PREFIX}:transfer_remove_menu:{athlete.id}",
                )
            ]
        )

    rows.append([InlineKeyboardButton("🔍 Найти другого спортсмена", callback_data=f"{MANAGER_CALLBACK_PREFIX}:transfer")])
    rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])
    return InlineKeyboardMarkup(rows)


async def _prompt_transfer_search(update: Update, context: ContextTypes.DEFAULT_TYPE, *, edit: bool = False, message=None) -> None:
    _set_manager_state(context, {"mode": "transfer_search"})
    text = "Введите фамилию или имя спортсмена (можно указать id)."
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")],
        ]
    )
    if edit and message:
        await message.edit_text(text, reply_markup=keyboard)
    else:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=keyboard,
        )


async def _send_transfer_search_results(update: Update, context: ContextTypes.DEFAULT_TYPE, query: str) -> None:
    athletes = await _search_athletes(query)
    if not athletes:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Спортсмены не найдены. Попробуйте другой запрос.",
        )
        return

    lines = ["Найдены спортсмены:"]
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    for athlete in athletes:
        person = athlete.person
        title = " ".join(filter(None, [person.surname, person.name])) or "Атлет"
        lines.append(f"• {title}")
        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    title,
                    callback_data=f"{MANAGER_CALLBACK_PREFIX}:transfer_select:{athlete.id}",
                )
            ]
        )

    keyboard_rows.append([InlineKeyboardButton("🔍 Искать снова", callback_data=f"{MANAGER_CALLBACK_PREFIX}:transfer")])
    keyboard_rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
    )


async def _send_athlete_details(update: Update, context: ContextTypes.DEFAULT_TYPE, athlete_id: int) -> None:
    athlete = await _load_athlete(athlete_id)
    if not athlete:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Спортсмен не найден.",
        )
        return

    _set_manager_state(context, None)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=_format_athlete_overview(athlete),
        reply_markup=_build_athlete_keyboard(athlete),
    )


async def _list_groups_for_selection(athlete: Athlete | None = None, *, include_current: bool = False) -> list[Group]:
    athlete_group_ids: set[int] = set()
    if athlete:
        athlete_group_ids = {group.id for group in athlete.groups.all()}

    def fetch():
        qs = Group.objects.order_by("name")
        groups = list(qs)
        if not include_current and athlete_group_ids:
            return [group for group in groups if group.id not in athlete_group_ids]
        if include_current and athlete_group_ids:
            return [group for group in groups if group.id in athlete_group_ids]
        if include_current:
            return []
        return groups

    return await sync_to_async(fetch, thread_sensitive=True)()


def _group_keyboard_rows(
    athlete_id: int,
    groups: list[Group],
    *,
    action: str,
    empty_text: str,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if not groups:
        rows.append([InlineKeyboardButton(empty_text, callback_data="manager:noop")])
    else:
        for group in groups[:MAX_SEARCH_RESULTS]:
            rows.append(
                [
                    InlineKeyboardButton(
                        group.name,
                        callback_data=f"{MANAGER_CALLBACK_PREFIX}:transfer_{action}:{athlete_id}:{group.id}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton("⬅️ К спортсмену", callback_data=f"{MANAGER_CALLBACK_PREFIX}:transfer_select:{athlete_id}")])
    rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])
    return InlineKeyboardMarkup(rows)


async def _prompt_add_group(update: Update, context: ContextTypes.DEFAULT_TYPE, athlete_id: int) -> None:
    athlete = await _load_athlete(athlete_id)
    if not athlete:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Спортсмен не найден.",
        )
        return

    groups = await _list_groups_for_selection(athlete, include_current=False)
    markup = _group_keyboard_rows(
        athlete_id,
        groups,
        action="add_to",
        empty_text="Нет доступных групп.",
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Выберите группу, в которую добавить спортсмена:",
        reply_markup=markup,
    )


async def _prompt_remove_group(update: Update, context: ContextTypes.DEFAULT_TYPE, athlete_id: int) -> None:
    athlete = await _load_athlete(athlete_id)
    if not athlete:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Спортсмен не найден.",
        )
        return

    groups = await _list_groups_for_selection(athlete, include_current=True)
    markup = _group_keyboard_rows(
        athlete_id,
        groups,
        action="remove_from",
        empty_text="Спортсмен не состоит ни в одной группе.",
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Выберите группу, из которой убрать спортсмена:",
        reply_markup=markup,
    )


async def _add_athlete_to_group(athlete_id: int, group_id: int) -> tuple[bool, str]:
    def operation():
        athlete = Athlete.objects.filter(id=athlete_id).first()
        group = Group.objects.filter(id=group_id).first()
        if not athlete or not group:
            return False, "Спортсмен или группа не найдены."
        if group.athletes.filter(id=athlete_id).exists():
            return False, f"Спортсмен уже находится в группе {group.name}."
        group.athletes.add(athlete)
        return True, f"Спортсмен добавлен в группу {group.name}."

    return await sync_to_async(operation, thread_sensitive=True)()


async def _remove_athlete_from_group(athlete_id: int, group_id: int) -> tuple[bool, str]:
    def operation():
        athlete = Athlete.objects.filter(id=athlete_id).first()
        group = Group.objects.filter(id=group_id).first()
        if not athlete or not group:
            return False, "Спортсмен или группа не найдены."
        if not group.athletes.filter(id=athlete_id).exists():
            return False, f"Спортсмен уже отсутствует в группе {group.name}."
        group.athletes.remove(athlete)
        return True, f"Спортсмен удалён из группы {group.name}."

    return await sync_to_async(operation, thread_sensitive=True)()


# ---------------------------------------------------------------------------
# Управление тренировками и записями
# ---------------------------------------------------------------------------

async def _load_manager_user(tg_id: int) -> User | None:
    user = await sync_to_async(
        User.objects.select_related("person").filter(tg_id=tg_id).first,
        thread_sensitive=True,
    )()
    if user:
        user.is_staff = True
    return user


async def _fetch_trainings_for_scope(user: User, scope: str) -> list:
    today = timezone.localdate()
    limit = TRAINING_SCOPE_DEFAULT_LIMIT
    if scope == "today":
        until = timezone.now() + timedelta(days=1)
        trainings = await sync_to_async(
            get_upcoming_trainings_for_user,
            thread_sensitive=True,
        )(user, limit=None, until=until)
        return [summary for summary in trainings if summary.start.date() == today]

    if scope == "tomorrow":
        tomorrow = today + timedelta(days=1)
        until = timezone.now() + timedelta(days=2)
        trainings = await sync_to_async(
            get_upcoming_trainings_for_user,
            thread_sensitive=True,
        )(user, limit=None, until=until)
        return [summary for summary in trainings if summary.start.date() == tomorrow]

    if scope == "week":
        until = timezone.now() + timedelta(days=7)
        return await sync_to_async(
            get_upcoming_trainings_for_user,
            thread_sensitive=True,
        )(user, limit=None, until=until)

    return await sync_to_async(
        get_upcoming_trainings_for_user,
        thread_sensitive=True,
    )(user, limit=limit)


def _format_training_list_item(summary) -> str:
    start = summary.start.strftime("%d.%m %H:%M")
    coach_icon = {
        "pending": "⏳",
        "planned": "✅",
        "cancelled": "❌",
    }.get(summary.coach_status, "🗓")
    return f"{coach_icon} {start} — {summary.group_name} ({summary.training_type_display})"


def _build_training_list_keyboard(trainings) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for summary in trainings:
        label = summary.start.strftime("%d.%m %H:%M")
        rows.append(
            [
                InlineKeyboardButton(
                    label,
                    callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:view:{summary.class_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])
    return InlineKeyboardMarkup(rows)


async def _send_training_list(update: Update, context: ContextTypes.DEFAULT_TYPE, scope: str) -> None:
    tg_id = update.effective_user.id if update.effective_user else None
    user = await _load_manager_user(tg_id)
    if not user:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Не удалось загрузить профиль менеджера.",
        )
        return

    trainings = await _fetch_trainings_for_scope(user, scope)
    if not trainings:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Подходящих тренировок не найдено.",
        )
        return

    lines = ["Тренировки:"]
    for summary in trainings:
        lines.append(_format_training_list_item(summary))

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="\n".join(lines),
        reply_markup=_build_training_list_keyboard(trainings),
    )


async def _load_training_summary(tg_id: int, class_id: int):
    user = await _load_manager_user(tg_id)
    if not user:
        return None, "Не удалось загрузить профиль менеджера."
    summary = await sync_to_async(
        get_training_summary,
        thread_sensitive=True,
    )(user, class_id)
    if not summary:
        return None, "Тренировка не найдена."
    return summary, None


def _format_training_detail(summary) -> str:
    local_start = summary.start.strftime("%d.%m %H:%M")
    lines = [
        f"{summary.emoji} {summary.training_type_display}",
        f"🗓 Дата и время: {local_start}",
        f"📍 Локация: {summary.location_display}",
        f"👥 Группа: {summary.group_name}",
    ]
    if summary.equipment_display:
        lines.append(f"🎒 Экипировка: {summary.equipment_display}")
    coach_hint = COACH_STATUS_HINTS.get(summary.coach_status)
    if coach_hint:
        lines.append(coach_hint)
    elif summary.coach_status_display:
        lines.append(f"Статус тренера: {summary.coach_status_display}")
    if summary.comment:
        lines.append("")
        lines.append(f"Комментарий: {summary.comment}")
    if summary.coach_comment:
        lines.append(f"Комментарий тренера: {summary.coach_comment}")
    if summary.athletes:
        lines.append("")
        lines.append("Записанные спортсмены:")
        for attendee in summary.athletes:
            icon = ATTENDANCE_STATUS_ICONS.get(attendee.status_key, "•")
            lines.append(f"{icon} {attendee.full_name} — {attendee.status_text}")
    else:
        lines.append("")
        lines.append("В тренировке пока нет записанных спортсменов.")
    return "\n".join(lines)


async def _update_training_fields(class_id: int, fields: dict[str, object]) -> tuple[bool, str]:
    if not fields:
        return False, "Не указаны параметры для обновления."

    def operation():
        class_instance = Class.objects.filter(id=class_id).first()
        if not class_instance:
            return False, "Тренировка не найдена."
        for field, value in fields.items():
            setattr(class_instance, field, value)
        class_instance.save(update_fields=list(fields.keys()))
        return True, ""

    return await sync_to_async(operation, thread_sensitive=True)()


def _build_training_edit_keyboard(summary) -> InlineKeyboardMarkup:
    class_id = summary.class_id
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                "🗓 Изменить дату и время",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:edit_schedule:{class_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "⏱ Изменить длительность",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:edit_duration:{class_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "📍 Изменить локацию",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:edit_location:{class_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🏷 Изменить вид тренировки",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:edit_type:{class_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "💬 Изменить комментарий",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:edit_comment:{class_id}",
            )
        ],
    ]
    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад к тренировке",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:view:{class_id}",
            )
        ]
    )
    rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])
    return InlineKeyboardMarkup(rows)


def _build_training_edit_prompt_keyboard(class_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "⬅️ Назад к редактированию",
                    callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:edit:{class_id}",
                )
            ],
            [InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")],
        ]
    )


def _build_training_location_keyboard(class_id: int, current_location: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for value, label in TrainingLocation.choices:
        prefix = "✅ " if value == current_location else ""
        rows.append(
            [
                InlineKeyboardButton(
                    f"{prefix}{label}",
                    callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:set_location:{class_id}:{value}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад к редактированию",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:edit:{class_id}",
            )
        ]
    )
    rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])
    return InlineKeyboardMarkup(rows)


def _build_training_type_keyboard(class_id: int, current_type: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for value, label in TrainingKind.choices:
        prefix = "✅ " if value == current_type else ""
        rows.append(
            [
                InlineKeyboardButton(
                    f"{prefix}{label}",
                    callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:set_type:{class_id}:{value}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад к редактированию",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:edit:{class_id}",
            )
        ]
    )
    rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])
    return InlineKeyboardMarkup(rows)


def _build_training_detail_keyboard(summary) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                "👥 Управлять записями",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:enrollments:{summary.class_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "➕ Добавить спортсмена",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:add_prompt:{summary.class_id}",
            )
        ],
    ]
    rows.append(
        [
            InlineKeyboardButton(
                "⚙️ Изменить тренировку",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:edit:{summary.class_id}",
            )
        ]
    )
    rows.append([InlineKeyboardButton("📋 К списку тренировок", callback_data=f"{MANAGER_CALLBACK_PREFIX}:trainings")])
    rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])
    return InlineKeyboardMarkup(rows)


async def _send_training_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, class_id: int) -> None:
    tg_id = update.effective_user.id if update.effective_user else None
    summary, error = await _load_training_summary(tg_id, class_id)
    if error:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=error,
        )
        return
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=_format_training_detail(summary),
        reply_markup=_build_training_detail_keyboard(summary),
    )


def _build_enrollment_list_keyboard(class_id: int, summary) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for attendee in summary.athletes:
        icon = ATTENDANCE_STATUS_ICONS.get(attendee.status_key, "•")
        rows.append(
            [
                InlineKeyboardButton(
                    f"{icon} {attendee.short_name}",
                    callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:select:{class_id}:{attendee.athlete_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("➕ Добавить спортсмена", callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:add_prompt:{class_id}")])
    rows.append([InlineKeyboardButton("⬅️ Назад к тренировке", callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:view:{class_id}")])
    rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])
    return InlineKeyboardMarkup(rows)


async def _send_enrollment_list(update: Update, context: ContextTypes.DEFAULT_TYPE, class_id: int) -> None:
    tg_id = update.effective_user.id if update.effective_user else None
    summary, error = await _load_training_summary(tg_id, class_id)
    if error:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=error,
        )
        return
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Список записанных спортсменов:",
        reply_markup=_build_enrollment_list_keyboard(class_id, summary),
    )


def _build_enrollment_action_keyboard(class_id: int, athlete_id: int) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                "✅ Подтвердить участие",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:attendance:{class_id}:{athlete_id}:confirm",
            )
        ],
        [
            InlineKeyboardButton(
                "❌ Отменить участие",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:attendance:{class_id}:{athlete_id}:decline",
            )
        ],
        [
            InlineKeyboardButton(
                "↺ Вернуть в ожидание",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:attendance:{class_id}:{athlete_id}:reset",
            )
        ],
        [
            InlineKeyboardButton(
                "🗑 Удалить из списка",
                callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:remove:{class_id}:{athlete_id}",
            )
        ],
        [InlineKeyboardButton("⬅️ Назад к списку", callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:enrollments:{class_id}")],
    ]
    rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])
    return InlineKeyboardMarkup(rows)


async def _send_enrollment_actions(update: Update, context: ContextTypes.DEFAULT_TYPE, class_id: int, athlete_id: int) -> None:
    tg_id = update.effective_user.id if update.effective_user else None
    summary, error = await _load_training_summary(tg_id, class_id)
    if error:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=error,
        )
        return
    athlete_name = None
    for attendee in summary.athletes:
        if attendee.athlete_id == athlete_id:
            athlete_name = attendee.full_name
            break
    if not athlete_name:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Спортсмен не найден в списке этой тренировки.",
        )
        return

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Выберите действие для {athlete_name}:",
        reply_markup=_build_enrollment_action_keyboard(class_id, athlete_id),
    )


async def _apply_attendance_action(update: Update, context: ContextTypes.DEFAULT_TYPE, class_id: int, athlete_id: int, action: str) -> None:
    tg_id = update.effective_user.id if update.effective_user else None
    user = await _load_manager_user(tg_id)
    if not user:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Не удалось загрузить профиль менеджера.",
        )
        return

    result = await sync_to_async(
        update_attendance_status,
        thread_sensitive=True,
    )(user, class_id, athlete_id, action)

    messages = {
        "ok": "Статус участия обновлён.",
        "not_found": "Запись на тренировку не найдена.",
        "forbidden": "У вас нет прав на изменение этой записи.",
        "invalid": "Неизвестное действие.",
    }
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=messages.get(result, "Не удалось обновить статус участия."),
    )


async def _remove_enrollment(class_id: int, athlete_id: int) -> bool:
    def operation():
        enrollment = (
            ClassEnrollment.objects.filter(class_instance_id=class_id, athlete_id=athlete_id).first()
        )
        if not enrollment:
            return False
        enrollment.delete()
        return True

    return await sync_to_async(operation, thread_sensitive=True)()


async def _prompt_training_add(update: Update, context: ContextTypes.DEFAULT_TYPE, class_id: int) -> None:
    _set_manager_state(context, {"mode": "training_add", "class_id": class_id})
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Введите фамилию спортсмена, которого нужно добавить на тренировку.",
    )


async def _add_athlete_to_training(class_id: int, athlete_id: int) -> tuple[bool, str]:
    def operation():
        class_instance = Class.objects.filter(id=class_id).first()
        athlete = Athlete.objects.filter(id=athlete_id).first()
        if not class_instance or not athlete:
            return False, "Тренировка или спортсмен не найдены."
        enrollment, created = ClassEnrollment.objects.get_or_create(
            class_instance=class_instance,
            athlete=athlete,
            defaults={
                "assistant_status": ClassEnrollment.ASSISTANT_STATUS_PENDING,
                "confirmed": False,
            },
        )
        if not created:
            return False, "Спортсмен уже записан на эту тренировку."
        return True, "Спортсмен добавлен на тренировку и отмечен как ожидающий подтверждения."

    return await sync_to_async(operation, thread_sensitive=True)()


# ---------------------------------------------------------------------------
# Обработка текстовых сообщений (по состояниям)
# ---------------------------------------------------------------------------

async def handle_manager_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    state = _get_manager_state(context)
    if not state:
        return False

    text = (update.effective_message.text or "").strip()
    if not text:
        await update.effective_message.reply_text("Пожалуйста, отправьте текст.")
        return True

    mode = state.get("mode")
    if mode == "family_search":
        await _send_family_search_results(update, context, text)
        return True

    if mode == "user_search":
        await _send_user_search_results(update, context, text)
        return True

    if mode == "transfer_search":
        await _send_transfer_search_results(update, context, text)
        return True

    if mode == "training_add":
        class_id = state.get("class_id")
        if not class_id:
            _set_manager_state(context, None)
            await update.effective_message.reply_text("Не удалось определить тренировку для добавления.")
            return True
        athletes = await _search_athletes(text)
        if not athletes:
            await update.effective_message.reply_text("Спортсмены не найдены. Попробуйте другой запрос.")
            return True
        rows: list[list[InlineKeyboardButton]] = []
        for athlete in athletes:
            person = athlete.person
            title = " ".join(filter(None, [person.surname, person.name])) or "Атлет"
            rows.append(
                [
                    InlineKeyboardButton(
                        title,
                        callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:add_select:{class_id}:{athlete.id}",
                    )
                ]
            )
        rows.append([InlineKeyboardButton("⬅️ Назад к тренировке", callback_data=f"{MANAGER_CALLBACK_PREFIX}:training:view:{class_id}")])
        rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Выберите спортсмена для добавления:",
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return True

    if mode == "training_edit_schedule":
        class_id = state.get("class_id")
        if not class_id:
            _set_manager_state(context, None)
            await update.effective_message.reply_text("Не удалось определить тренировку для изменения.")
            return True
        try:
            new_dt = datetime.strptime(text, "%d.%m.%Y %H:%M")
        except ValueError:
            await update.effective_message.reply_text(
                "Не удалось распознать дату. Используйте формат «ДД.ММ.ГГГГ ЧЧ:ММ»."
            )
            return True
        aware_dt = timezone.make_aware(new_dt, timezone.get_current_timezone())
        success, error_message = await _update_training_fields(class_id, {"date": aware_dt})
        if not success:
            await update.effective_message.reply_text(error_message or "Не удалось обновить дату и время.")
            return True
        _set_manager_state(context, None)
        await update.effective_message.reply_text("Дата и время тренировки обновлены.")
        await _send_training_detail(update, context, class_id)
        return True

    if mode == "training_edit_duration":
        class_id = state.get("class_id")
        if not class_id:
            _set_manager_state(context, None)
            await update.effective_message.reply_text("Не удалось определить тренировку для изменения.")
            return True
        try:
            minutes = int(text)
        except ValueError:
            await update.effective_message.reply_text("Укажите длительность числом в минутах.")
            return True
        if minutes <= 0 or minutes > 600:
            await update.effective_message.reply_text("Длительность должна быть от 1 до 600 минут.")
            return True
        success, error_message = await _update_training_fields(class_id, {"duration": minutes})
        if not success:
            await update.effective_message.reply_text(error_message or "Не удалось обновить длительность.")
            return True
        _set_manager_state(context, None)
        await update.effective_message.reply_text("Длительность тренировки обновлена.")
        await _send_training_detail(update, context, class_id)
        return True

    if mode == "training_edit_comment":
        class_id = state.get("class_id")
        if not class_id:
            _set_manager_state(context, None)
            await update.effective_message.reply_text("Не удалось определить тренировку для изменения.")
            return True
        if text == "-":
            new_comment = None
        else:
            new_comment = text
        success, error_message = await _update_training_fields(class_id, {"comment": new_comment})
        if not success:
            await update.effective_message.reply_text(error_message or "Не удалось обновить комментарий.")
            return True
        _set_manager_state(context, None)
        await update.effective_message.reply_text("Комментарий обновлён.")
        await _send_training_detail(update, context, class_id)
        return True

    return False


# ---------------------------------------------------------------------------
# Callback обработчик
# ---------------------------------------------------------------------------

async def handle_manager_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if not await _ensure_manager_access(update, context):
        return

    tg_id = update.effective_user.id if update.effective_user else None

    data = query.data or ""
    if data == f"{MANAGER_CALLBACK_PREFIX}:menu":
        await manager_panel(update, context)
        return

    if data == f"{MANAGER_CALLBACK_PREFIX}:families":
        await _prompt_family_search(update, context, edit=False)
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:family:"):
        try:
            family_id = int(data.split(":", 2)[2])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить семью.")
            return
        await _send_family_details(update, context, family_id)
        return

    if data == f"{MANAGER_CALLBACK_PREFIX}:users":
        await _prompt_user_search(update, context, edit=False)
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:user:"):
        try:
            user_id = int(data.split(":", 2)[2])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить пользователя.")
            return
        await _send_user_details(update, context, user_id)
        return

    if data == f"{MANAGER_CALLBACK_PREFIX}:approvals":
        await _send_pending_overview(update, context)
        return

    if data == f"{MANAGER_CALLBACK_PREFIX}:transfer":
        await _prompt_transfer_search(update, context, edit=False)
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:transfer_family:"):
        try:
            family_id = int(data.split(":", 2)[2])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить семью.")
            return

        def fetch_family_athletes():
            return list(
                Athlete.objects.filter(person__families__id=family_id)
                .select_related("person")
                .order_by("person__surname", "person__name")
            )

        athletes = await sync_to_async(fetch_family_athletes, thread_sensitive=True)()
        if not athletes:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="В этой семье нет спортсменов.",
            )
            return

        rows: list[list[InlineKeyboardButton]] = []
        lines = ["Спортсмены семьи:"]
        for athlete in athletes:
            person = athlete.person
            title = " ".join(filter(None, [person.surname, person.name])) or "Атлет"
            lines.append(f"• {title}")
            rows.append(
                [
                    InlineKeyboardButton(
                        title,
                        callback_data=f"{MANAGER_CALLBACK_PREFIX}:transfer_select:{athlete.id}",
                    )
                ]
            )
        rows.append([InlineKeyboardButton("⬅️ К семье", callback_data=f"{MANAGER_CALLBACK_PREFIX}:family:{family_id}")])
        rows.append([InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")])
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="\n".join(lines),
            reply_markup=InlineKeyboardMarkup(rows),
        )
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:transfer_select:"):
        try:
            athlete_id = int(data.split(":", 2)[2])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить спортсмена.")
            return
        await _send_athlete_details(update, context, athlete_id)
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:transfer_add:"):
        try:
            athlete_id = int(data.split(":", 2)[2])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить спортсмена.")
            return
        await _prompt_add_group(update, context, athlete_id)
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:transfer_add_to:"):
        parts = data.split(":")
        if len(parts) != 4:
            await query.edit_message_text("Некорректный запрос.")
            return
        try:
            athlete_id = int(parts[2])
            group_id = int(parts[3])
        except ValueError:
            await query.edit_message_text("Некорректный идентификатор.")
            return
        success, message = await _add_athlete_to_group(athlete_id, group_id)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=message)
        if success:
            await _send_athlete_details(update, context, athlete_id)
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:transfer_remove_menu:"):
        try:
            athlete_id = int(data.split(":", 2)[2])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить спортсмена.")
            return
        await _prompt_remove_group(update, context, athlete_id)
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:transfer_remove_from:"):
        parts = data.split(":")
        if len(parts) != 4:
            await query.edit_message_text("Некорректный запрос.")
            return
        try:
            athlete_id = int(parts[2])
            group_id = int(parts[3])
        except ValueError:
            await query.edit_message_text("Некорректный идентификатор.")
            return
        success, message = await _remove_athlete_from_group(athlete_id, group_id)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=message)
        if success:
            await _send_athlete_details(update, context, athlete_id)
        return

    if data == f"{MANAGER_CALLBACK_PREFIX}:trainings":
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Сегодня", callback_data=f"{MANAGER_CALLBACK_PREFIX}:trainings:list:today"),
                    InlineKeyboardButton("Завтра", callback_data=f"{MANAGER_CALLBACK_PREFIX}:trainings:list:tomorrow"),
                ],
                [
                    InlineKeyboardButton("7 дней вперёд", callback_data=f"{MANAGER_CALLBACK_PREFIX}:trainings:list:week"),
                ],
                [InlineKeyboardButton("🏠 Панель менеджера", callback_data=f"{MANAGER_CALLBACK_PREFIX}:menu")],
            ]
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Выберите интервал для просмотра тренировок:",
            reply_markup=keyboard,
        )
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:trainings:list:"):
        try:
            scope = data.split(":", 3)[3]
        except IndexError:
            await query.edit_message_text("Не удалось определить интервал.")
            return
        await _send_training_list(update, context, scope)
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:edit_schedule:"):
        try:
            class_id = int(data.split(":", 3)[3])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить тренировку.")
            return
        summary, error = await _load_training_summary(tg_id, class_id)
        if error:
            await query.edit_message_text(error)
            return
        _set_manager_state(context, {"mode": "training_edit_schedule", "class_id": class_id})
        current_value = summary.start.strftime("%d.%m.%Y %H:%M")
        text = (
            f"{summary.emoji} {summary.training_type_display}\n"
            f"Текущие дата и время: {current_value}\n\n"
            "Отправьте новое значение в формате «ДД.ММ.ГГГГ ЧЧ:ММ»."
        )
        await query.edit_message_text(
            text,
            reply_markup=_build_training_edit_prompt_keyboard(class_id),
        )
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:edit_duration:"):
        try:
            class_id = int(data.split(":", 3)[3])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить тренировку.")
            return
        summary, error = await _load_training_summary(tg_id, class_id)
        if error:
            await query.edit_message_text(error)
            return
        _set_manager_state(context, {"mode": "training_edit_duration", "class_id": class_id})
        current_duration = summary.duration_minutes
        text = (
            f"{summary.emoji} {summary.training_type_display}\n"
            f"Текущая длительность: {current_duration} мин.\n\n"
            "Отправьте новое значение в минутах (целое число)."
        )
        await query.edit_message_text(
            text,
            reply_markup=_build_training_edit_prompt_keyboard(class_id),
        )
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:edit_comment:"):
        try:
            class_id = int(data.split(":", 3)[3])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить тренировку.")
            return
        summary, error = await _load_training_summary(tg_id, class_id)
        if error:
            await query.edit_message_text(error)
            return
        _set_manager_state(context, {"mode": "training_edit_comment", "class_id": class_id})
        current_comment = (summary.comment or "Комментарий отсутствует.").strip()
        if len(current_comment) > 200:
            current_comment = current_comment[:200] + "…"
        text = (
            f"{summary.emoji} {summary.training_type_display}\n"
            f"Сейчас: {current_comment}\n\n"
            "Отправьте новый комментарий. Чтобы очистить поле, отправьте «-»."
        )
        await query.edit_message_text(
            text,
            reply_markup=_build_training_edit_prompt_keyboard(class_id),
        )
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:edit_location:"):
        try:
            class_id = int(data.split(":", 3)[3])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить тренировку.")
            return
        summary, error = await _load_training_summary(tg_id, class_id)
        if error:
            await query.edit_message_text(error)
            return
        _set_manager_state(context, None)
        text = (
            f"{summary.emoji} {summary.training_type_display}\n"
            f"Текущая локация: {summary.location_display}\n\n"
            "Выберите новую локацию:"
        )
        await query.edit_message_text(
            text,
            reply_markup=_build_training_location_keyboard(class_id, summary.location),
        )
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:edit_type:"):
        try:
            class_id = int(data.split(":", 3)[3])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить тренировку.")
            return
        summary, error = await _load_training_summary(tg_id, class_id)
        if error:
            await query.edit_message_text(error)
            return
        _set_manager_state(context, None)
        text = (
            f"{summary.emoji} {summary.training_type_display}\n"
            f"Текущий вид: {summary.training_type_display}\n\n"
            "Выберите новый вид тренировки:"
        )
        await query.edit_message_text(
            text,
            reply_markup=_build_training_type_keyboard(class_id, summary.training_type),
        )
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:set_location:"):
        parts = data.split(":")
        if len(parts) != 5:
            await query.edit_message_text("Некорректный запрос.")
            return
        try:
            class_id = int(parts[3])
        except ValueError:
            await query.edit_message_text("Некорректный идентификатор.")
            return
        new_value = parts[4]
        valid_locations = {value for value, _ in TrainingLocation.choices}
        if new_value not in valid_locations:
            await query.answer("Некорректная локация", show_alert=True)
            return
        success, error_message = await _update_training_fields(class_id, {"location": new_value})
        if not success:
            await query.answer(error_message or "Не удалось обновить локацию.", show_alert=True)
            return
        _set_manager_state(context, None)
        await query.answer("Локация обновлена")
        summary, error = await _load_training_summary(tg_id, class_id)
        if error:
            await query.edit_message_text(error)
            return
        await query.edit_message_text(
            _format_training_detail(summary),
            reply_markup=_build_training_detail_keyboard(summary),
        )
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:set_type:"):
        parts = data.split(":")
        if len(parts) != 5:
            await query.edit_message_text("Некорректный запрос.")
            return
        try:
            class_id = int(parts[3])
        except ValueError:
            await query.edit_message_text("Некорректный идентификатор.")
            return
        new_value = parts[4]
        valid_types = {value for value, _ in TrainingKind.choices}
        if new_value not in valid_types:
            await query.answer("Некорректный вид тренировки", show_alert=True)
            return
        success, error_message = await _update_training_fields(class_id, {"training_type": new_value})
        if not success:
            await query.answer(error_message or "Не удалось обновить тренировку.", show_alert=True)
            return
        _set_manager_state(context, None)
        await query.answer("Вид тренировки обновлён")
        summary, error = await _load_training_summary(tg_id, class_id)
        if error:
            await query.edit_message_text(error)
            return
        await query.edit_message_text(
            _format_training_detail(summary),
            reply_markup=_build_training_detail_keyboard(summary),
        )
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:edit:"):
        try:
            class_id = int(data.split(":", 3)[3])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить тренировку.")
            return
        summary, error = await _load_training_summary(tg_id, class_id)
        if error:
            await query.edit_message_text(error)
            return
        _set_manager_state(context, None)
        await query.edit_message_text(
            f"{_format_training_detail(summary)}\n\nВыберите параметр для изменения:",
            reply_markup=_build_training_edit_keyboard(summary),
        )
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:view:"):
        try:
            class_id = int(data.split(":", 3)[3])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить тренировку.")
            return
        await _send_training_detail(update, context, class_id)
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:enrollments:"):
        try:
            class_id = int(data.split(":", 3)[3])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить тренировку.")
            return
        await _send_enrollment_list(update, context, class_id)
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:select:"):
        parts = data.split(":")
        if len(parts) != 5:
            await query.edit_message_text("Некорректный запрос.")
            return
        try:
            class_id = int(parts[3])
            athlete_id = int(parts[4])
        except ValueError:
            await query.edit_message_text("Некорректный идентификатор.")
            return
        await _send_enrollment_actions(update, context, class_id, athlete_id)
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:attendance:"):
        parts = data.split(":")
        if len(parts) != 6:
            await query.edit_message_text("Некорректный запрос.")
            return
        try:
            class_id = int(parts[3])
            athlete_id = int(parts[4])
        except ValueError:
            await query.edit_message_text("Некорректный идентификатор.")
            return
        action = parts[5]
        await _apply_attendance_action(update, context, class_id, athlete_id, action)
        await _send_enrollment_list(update, context, class_id)
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:remove:"):
        parts = data.split(":")
        if len(parts) != 5:
            await query.edit_message_text("Некорректный запрос.")
            return
        try:
            class_id = int(parts[3])
            athlete_id = int(parts[4])
        except ValueError:
            await query.edit_message_text("Некорректный идентификатор.")
            return
        removed = await _remove_enrollment(class_id, athlete_id)
        if removed:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Спортсмен удалён из списка тренировки.",
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="Запись на тренировку не найдена.",
            )
        await _send_enrollment_list(update, context, class_id)
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:add_prompt:"):
        try:
            class_id = int(data.split(":", 3)[3])
        except (IndexError, ValueError):
            await query.edit_message_text("Не удалось определить тренировку.")
            return
        await _prompt_training_add(update, context, class_id)
        return

    if data.startswith(f"{MANAGER_CALLBACK_PREFIX}:training:add_select:"):
        parts = data.split(":")
        if len(parts) != 5:
            await query.edit_message_text("Некорректный запрос.")
            return
        try:
            class_id = int(parts[3])
            athlete_id = int(parts[4])
        except ValueError:
            await query.edit_message_text("Некорректный идентификатор.")
            return
        success, message = await _add_athlete_to_training(class_id, athlete_id)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=message)
        if success:
            await _send_enrollment_list(update, context, class_id)
        return

    if data == "manager:noop":
        return

    await query.edit_message_text("Действие пока не поддерживается.")


async def cancel_manager_state(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    state = _get_manager_state(context)
    if not state:
        return False

    _set_manager_state(context, None)
    await update.effective_message.reply_text("Действие менеджера отменено.")
    return True
