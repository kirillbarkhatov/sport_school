from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from typing import Sequence

from asgiref.sync import sync_to_async
from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from school.models import Athlete as AthleteModel

from users.models import User
from users.tasks import notify_pending_user_task
from tg_bot.services.user_sync import update_user_comment_async
from tg_bot.services.family_overview import get_family_overview
from tg_bot.services.training_overview import (
    build_training_details_text,
    get_training_summary,
    get_upcoming_trainings_for_user,
    update_attendance_status,
)
from tg_bot.handlers.auth import build_authenticated_keyboard
from users.utils import get_person_queryset_for_user
from users.services import normalize_phone

FAMILY_EDIT_STATE_KEY = "family_edit_state"
COMMENT_STATE_KEY = "awaiting_comment"

PERSON_FIELD_CONFIG = {
    "surname": {
        "button": "Фамилия",
        "title": "Фамилия",
        "attr": "surname",
        "type": "text",
        "allow_clear": False,
        "prompt": "Введите новую фамилию (сейчас: {current}).",
        "success": "Готово! Фамилия теперь: {value}.",
        "model": "person",
    },
    "name": {
        "button": "Имя",
        "title": "Имя",
        "attr": "name",
        "type": "text",
        "allow_clear": False,
        "prompt": "Введите новое имя (сейчас: {current}).",
        "success": "Готово! Имя теперь: {value}.",
        "model": "person",
    },
    "middlename": {
        "button": "Отчество",
        "title": "Отчество",
        "attr": "middlename",
        "type": "text",
        "allow_clear": True,
        "prompt": "Введите новое отчество (сейчас: {current}).",
        "success": "Обновили отчество: {value}.",
        "model": "person",
    },
    "date_of_birth": {
        "button": "Дата рождения",
        "title": "Дата рождения",
        "attr": "date_of_birth",
        "type": "date",
        "allow_clear": True,
        "prompt": "Введите дату рождения в формате ДД.ММ.ГГГГ (сейчас: {current}).",
        "success": "Дата рождения обновлена: {value}.",
        "model": "person",
    },
    "email": {
        "button": "Почта",
        "title": "Почта",
        "attr": "email",
        "type": "email",
        "allow_clear": True,
        "prompt": "Введите новую почту (сейчас: {current}).",
        "success": "Почта обновлена: {value}.",
        "model": "person",
    },
    "phone": {
        "button": "Телефон",
        "title": "Телефон",
        "attr": "phone",
        "type": "phone",
        "allow_clear": True,
        "prompt": "Введите телефон, например +7 921 123-45-67 (сейчас: {current}).",
        "success": "Телефон обновлён: {value}.",
        "model": "person",
    },
}

PERSON_FIELD_ROWS = [
    ("surname", "name"),
    ("middlename", "date_of_birth"),
    ("email", "phone"),
]

ATHLETE_LEVEL_LABELS = {code: label for code, label in AthleteModel.LEVEL_CHOICES}
LEVEL_INPUT_PATTERN = re.compile(r"^(20\d{2})\s*[-/]\s*(20\d{2})$")


ATHLETE_FIELD_CONFIG = {
    "athlete_level": {
        "button": "Сезон старта",
        "title": "Сезон старта",
        "attr": "level",
        "type": "athlete_level",
        "allow_clear": False,
        "prompt": "Введите сезон в формате 2024/2025 (сейчас: {current}).",
        "success": "Сезон старта обновлён: {value}.",
        "model": "athlete",
    },
    "athlete_rank": {
        "button": "Разряд",
        "title": "Разряд",
        "attr": "rank",
        "type": "text",
        "allow_clear": True,
        "prompt": "Введите спортивный разряд (сейчас: {current}).",
        "success": "Разряд обновлён: {value}.",
        "model": "athlete",
    },
    "athlete_comment": {
        "button": "Комментарий",
        "title": "Комментарий по спортсмену",
        "attr": "comment",
        "type": "multiline",
        "allow_clear": True,
        "prompt": "Введите комментарий по спортсмену (сейчас: {current}).",
        "success": "Комментарий обновлён.",
        "model": "athlete",
    },
}

ATHLETE_FIELD_ROWS = [
    ("athlete_level", "athlete_rank"),
    ("athlete_comment",),
]

logger = logging.getLogger(__name__)


async def _load_user(tg_id: int) -> User | None:
    return await sync_to_async(
        User.objects.select_related("person").filter(tg_id=tg_id).first,
        thread_sensitive=True,
    )()


async def _clear_query_keyboard(query) -> None:
    if not query or not query.message:
        return
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except BadRequest:
        pass


async def _clear_family_prompt_keyboard(bot, state: dict | None) -> None:
    if not state:
        return
    chat_id = state.get("chat_id")
    message_id = state.get("prompt_message_id")
    if not chat_id or not message_id:
        return
    try:
        await bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=None)
    except BadRequest:
        pass


async def _send_main_menu(bot, chat_id: int, user: User) -> None:
    await bot.send_message(
        chat_id=chat_id,
        text="Выберите действие:",
        reply_markup=build_authenticated_keyboard(),
    )


def _get_editor_messages_store(context: ContextTypes.DEFAULT_TYPE) -> dict[int, dict[str, int]]:
    return context.user_data.setdefault("family_editor_messages", {})


def _normalize_level_choice(raw_value: str) -> str | None:
    value = raw_value.strip()
    match = LEVEL_INPUT_PATTERN.match(value)
    if not match:
        return None
    start, end = match.groups()
    key = f"{start}-{end}"
    if key not in ATHLETE_LEVEL_LABELS:
        return None
    return key


def _format_full_name(person) -> str:
    parts = [person.surname, person.name, person.middlename]
    return " ".join(part for part in parts if part).strip() or "Без имени"


def _format_value_for_display(value, field_config: dict[str, str]) -> str:
    if value in (None, ""):
        return "—"
    field_type = field_config.get("type")
    if field_type == "date":
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date):
            return value.strftime("%d.%m.%Y")
    if field_type == "phone":
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        if len(digits) == 11 and digits.startswith("7"):
            return f"+{digits}"
        if str(value).startswith("+"):
            return str(value)
    if field_type == "athlete_level":
        return ATHLETE_LEVEL_LABELS.get(str(value), str(value))
    return str(value)


def _build_member_editor_keyboard(person) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    for field_row in PERSON_FIELD_ROWS:
        buttons: list[InlineKeyboardButton] = []
        for field_key in field_row:
            config = PERSON_FIELD_CONFIG.get(field_key)
            if not config:
                continue
            buttons.append(
                InlineKeyboardButton(
                    config["button"],
                    callback_data=f"family:field:{person.id}:{field_key}",
                )
            )
        if buttons:
            rows.append(buttons)

    athlete = getattr(person, "athlete", None)
    if athlete:
        for field_row in ATHLETE_FIELD_ROWS:
            buttons = []
            for field_key in field_row:
                config = ATHLETE_FIELD_CONFIG.get(field_key)
                if not config:
                    continue
                buttons.append(
                    InlineKeyboardButton(
                        config["button"],
                        callback_data=f"family:field:{person.id}:{field_key}",
                    )
                )
            if buttons:
                rows.append(buttons)

    rows.append([InlineKeyboardButton("◀️ К списку семьи", callback_data="user:family")])
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="user:menu")])
    return InlineKeyboardMarkup(rows)


def _compose_member_editor_text(person) -> str:
    lines: list[str] = [
        _format_full_name(person),
        "",
        "Текущие данные:",
    ]

    for field_key in ["surname", "name", "middlename", "date_of_birth", "email", "phone"]:
        config = PERSON_FIELD_CONFIG[field_key]
        value = getattr(person, config["attr"])
        lines.append(f"• {config['title']}: {_format_value_for_display(value, config)}")

    athlete = getattr(person, "athlete", None)
    if athlete:
        lines.append("")
        lines.append("Данные спортсмена:")
        level_display = athlete.get_level_display() if hasattr(athlete, "get_level_display") else ""
        lines.append(f"• Катается с сезона: {level_display or '—'}")
        for field_key in ["athlete_rank", "athlete_comment"]:
            config = ATHLETE_FIELD_CONFIG[field_key]
            value = getattr(athlete, config["attr"], None)
            lines.append(f"• {config['title']}: {_format_value_for_display(value, config)}")

    lines.append("")
    lines.append("Выберите, что хотите изменить:")
    return "\n".join(lines)


async def _get_member_editor_data(user: User, person_id: int):
    def fetch():
        qs = (
            get_person_queryset_for_user(user)
            .select_related("athlete")
            .filter(id=person_id)
        )
        return qs.first()

    person = await sync_to_async(fetch, thread_sensitive=True)()
    if not person:
        return None, None, None

    text = _compose_member_editor_text(person)
    markup = _build_member_editor_keyboard(person)
    return person, text, markup


def _parse_field_input(config: dict[str, str], raw_value: str) -> tuple[object | None, str | None]:
    value = raw_value.strip()
    if not value and config.get("type") != "multiline":
        return None, "Отправьте значение или '-' чтобы очистить поле."

    if value == "-":
        if not config.get("allow_clear", True):
            return None, "Это поле нельзя очистить."
        return None, None

    field_type = config.get("type")
    if field_type == "text":
        return value, None
    if field_type == "multiline":
        return raw_value.strip(), None
    if field_type == "date":
        try:
            parsed = datetime.strptime(value, "%d.%m.%Y").date()
        except ValueError:
            return None, "Не удалось распознать дату. Используйте формат ДД.ММ.ГГГГ."
        return parsed, None
    if field_type == "email":
        if "@" not in value or "." not in value.split("@")[-1]:
            return None, "Похоже, это не адрес почты. Проверьте формат и попробуйте снова."
        return value, None
    if field_type == "phone":
        normalized = normalize_phone(value)
        if not normalized:
            return None, "Не удалось распознать номер. Укажите телефон целиком, например +7 921 123-45-67."
        return normalized, None
    if field_type == "athlete_level":
        normalized_level = _normalize_level_choice(value)
        if not normalized_level:
            return None, "Используйте формат 2024/2025 и выберите сезон из списка."
        return normalized_level, None
    return value, None


async def _apply_family_edit(user: User, person_id: int, field_key: str, raw_value: str) -> tuple[bool, str]:
    config = PERSON_FIELD_CONFIG.get(field_key) or ATHLETE_FIELD_CONFIG.get(field_key)
    if not config:
        return False, "Это поле недоступно для редактирования."

    def fetch():
        qs = (
            get_person_queryset_for_user(user)
            .select_related("athlete")
            .filter(id=person_id)
        )
        return qs.first()

    person = await sync_to_async(fetch, thread_sensitive=True)()
    if not person:
        return False, "Не удалось найти этого члена семьи."

    target = person if config.get("model") == "person" else getattr(person, "athlete", None)
    if target is None:
        return False, "Для этого члена семьи нет данных спортсмена."

    new_value, error = _parse_field_input(config, raw_value)
    if error:
        return False, error

    attr = config["attr"]
    current_value = getattr(target, attr)
    if isinstance(current_value, datetime):
        current_value = current_value.date()
    if new_value is None and not config.get("allow_clear", True):
        return False, "Это поле нельзя очистить."
    if new_value == "":
        new_value = None

    if current_value == new_value:
        return False, "Значение не изменилось."

    setattr(target, attr, new_value)
    await sync_to_async(target.save, thread_sensitive=True)(update_fields=[attr])

    display_value = _format_value_for_display(new_value, config)
    success_message = config.get("success", "Данные обновлены.").format(value=display_value)
    return True, success_message


async def _send_family_member_editor(
    query,
    user: User,
    person_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    edit: bool = False,
) -> None:
    person, text, markup = await _get_member_editor_data(user, person_id)
    if not person:
        if query and query.message:
            await query.message.reply_text("Не удалось найти этого члена семьи.")
        return

    editor_messages = _get_editor_messages_store(context)

    if edit and query.message:
        await query.message.edit_text(text, reply_markup=markup)
        editor_messages[person_id] = {
            "chat_id": query.message.chat_id,
            "message_id": query.message.message_id,
        }
    else:
        await _clear_query_keyboard(query)
        message = await query.message.reply_text(text, reply_markup=markup)
        editor_messages[person_id] = {
            "chat_id": message.chat_id,
            "message_id": message.message_id,
        }


async def _update_family_member_editor_card(
    bot,
    context: ContextTypes.DEFAULT_TYPE,
    user: User,
    person_id: int,
    chat_id: int,
) -> None:
    person, text, markup = await _get_member_editor_data(user, person_id)
    if not person:
        return

    editor_messages = _get_editor_messages_store(context)
    meta = editor_messages.get(person_id)
    target_chat = chat_id
    target_message_id = None
    if meta:
        target_chat = meta.get("chat_id", chat_id)
        target_message_id = meta.get("message_id")

    if target_message_id:
        try:
            await bot.edit_message_text(
                chat_id=target_chat,
                message_id=target_message_id,
                text=text,
                reply_markup=markup,
            )
            return
        except BadRequest:
            pass

    message = await bot.send_message(chat_id=target_chat, text=text, reply_markup=markup)
    editor_messages[person_id] = {
        "chat_id": message.chat_id,
        "message_id": message.message_id,
    }

def _format_schedule_list_text(trainings) -> str:
    if not trainings:
        return "На ближайшую неделю тренировки не найдены."

    lines = ["Выберите тренировку, чтобы обновить статус участия:"]
    for idx, summary in enumerate(trainings, start=1):
        date_text = summary.start.strftime("%d.%m %H:%M")
        lines.append(
            f"{idx}. {summary.emoji} {summary.training_type_display} — {date_text} ({summary.group_name})"
        )
    return "\n".join(lines)


def _build_schedule_keyboard(trainings) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for summary in trainings:
        date_text = summary.start.strftime("%d.%m %H:%M")
        label = f"{summary.emoji} {date_text}"
        rows.append(
            [InlineKeyboardButton(label, callback_data=f"schedule:view:{summary.class_id}")]
        )
    rows.append([InlineKeyboardButton("🔄 Обновить", callback_data="schedule:list")])
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="user:menu")])
    return InlineKeyboardMarkup(rows)


def _attendance_callback_data(class_id: int, athlete_id: int, action: str, origin: str) -> str:
    return f"attendance:{class_id}:{athlete_id}:{action}:{origin}"


def _build_attendance_keyboard(
    summary,
    *,
    origin: str,
    back_callback: str | None = None,
    extra_rows: Sequence[list[InlineKeyboardButton]] | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    if summary.athletes:
        for attendee in summary.athletes:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"✅ {attendee.short_name}",
                        callback_data=_attendance_callback_data(
                            summary.class_id, attendee.athlete_id, "confirm", origin
                        ),
                    ),
                    InlineKeyboardButton(
                        f"❌ {attendee.short_name}",
                        callback_data=_attendance_callback_data(
                            summary.class_id, attendee.athlete_id, "decline", origin
                        ),
                    ),
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        f"⏳ {attendee.short_name}",
                        callback_data=_attendance_callback_data(
                            summary.class_id, attendee.athlete_id, "reset", origin
                        ),
                    )
                ]
            )
    if extra_rows:
        rows.extend(extra_rows)
    if back_callback:
        rows.append([InlineKeyboardButton("◀️ Назад", callback_data=back_callback)])
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="user:menu")])
    return InlineKeyboardMarkup(rows)


async def _send_schedule_overview(query, user: User, *, edit: bool = False) -> None:
    until = timezone.now() + timedelta(days=7)
    trainings = await sync_to_async(
        get_upcoming_trainings_for_user,
        thread_sensitive=True,
    )(user, limit=10, until=until)

    text = _format_schedule_list_text(trainings)
    markup = _build_schedule_keyboard(trainings)

    if edit and query.message:
        await query.message.edit_text(text, reply_markup=markup)
    else:
        await _clear_query_keyboard(query)
        await query.message.reply_text(text, reply_markup=markup)


async def _send_training_detail(
    query,
    user: User,
    class_id: int,
    *,
    origin: str,
    edit: bool = False,
) -> None:
    summary = await sync_to_async(
        get_training_summary,
        thread_sensitive=True,
    )(user, class_id)

    if not summary:
        message = "Не удалось найти запрошенную тренировку. Возможно, она больше недоступна."
        if edit and query.message:
            await query.message.edit_text(message)
        else:
            await query.message.reply_text(message)
        return

    text = build_training_details_text(summary)

    extra_rows: list[list[InlineKeyboardButton]] = []
    back_callback = None
    if origin == "plan":
        extra_rows.append([InlineKeyboardButton("📅 Все тренировки", callback_data="user:schedule")])
    if origin == "schedule":
        back_callback = "schedule:list"

    markup = _build_attendance_keyboard(
        summary,
        origin=origin,
        back_callback=back_callback,
        extra_rows=extra_rows,
    )

    if edit and query.message:
        await query.message.edit_text(text, reply_markup=markup)
    else:
        await _clear_query_keyboard(query)
        await query.message.reply_text(text, reply_markup=markup)


async def _send_family_overview(query, user: User) -> None:
    overviews = await sync_to_async(
        get_family_overview,
        thread_sensitive=True,
    )(user)

    if not overviews:
        await query.message.reply_text(
            "Мы пока не нашли данных о вашей семье. Если считаете, что это ошибка, напишите администратору."
        )
        return

    keyboard_rows: list[list[InlineKeyboardButton]] = []
    text_blocks: list[str] = []

    for overview in overviews:
        block_lines = [f"Семья — {overview.family_name}"]
        for member in overview.members:
            block_lines.append(f"• {member.title}")
            if member.athlete_extra:
                block_lines.append(f"  {member.athlete_extra}")
            if member.person_id:
                keyboard_rows.append(
                    [
                        InlineKeyboardButton(
                            f"✏️ {member.title}",
                            callback_data=f"family:edit:{member.person_id}",
                        )
                    ]
                )
        text_blocks.append("\n".join(block_lines))

    keyboard_rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="user:menu")])

    text = "\n\n".join(text_blocks)
    await _clear_query_keyboard(query)
    await query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard_rows))


async def handle_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data or ""
    await query.answer()

    tg_id = update.effective_user.id

    if data == "user:comment":
        context.user_data[COMMENT_STATE_KEY] = True
        await query.message.reply_text(
            "Напишите сообщение – мы передадим его администратору."
        )
        return

    if data == "user:menu":
        state = context.user_data.pop(FAMILY_EDIT_STATE_KEY, None)
        await _clear_family_prompt_keyboard(context.bot, state)
        context.user_data.pop("family_editor_messages", None)
        await _clear_query_keyboard(query)
        user = await _load_user(tg_id)
        if not user:
            await query.message.reply_text(
                "Не удалось найти ваш профиль. Нажмите /start и попробуйте снова."
            )
            return
        await _send_main_menu(context.bot, query.message.chat_id, user)
        return

    actionable = (
        data in {"user:schedule", "schedule:list", "user:plan", "user:family", "user:camps", "user:payments"}
        or data.startswith(
            (
                "schedule:view:",
                "attendance:",
                "family:",
            )
        )
    )

    if actionable:
        user = await _load_user(tg_id)
        if not user:
            await query.message.reply_text(
                "Не удалось найти ваш профиль. Нажмите /start и попробуйте снова."
            )
            return

        if data == "user:schedule":
            await _send_schedule_overview(query, user)
            return

        if data == "schedule:list":
            await _send_schedule_overview(query, user, edit=True)
            return

        if data.startswith("schedule:view:"):
            try:
                class_id = int(data.split(":", 2)[2])
            except (ValueError, IndexError):
                await query.message.reply_text("Не удалось распознать выбранную тренировку.")
                return
            await _send_training_detail(query, user, class_id, origin="schedule", edit=True)
            return

        if data.startswith("attendance:"):
            parts = data.split(":")
            if len(parts) != 5:
                await query.answer("Неизвестный формат данных.", show_alert=True)
                return
            _, class_id_raw, athlete_id_raw, action, origin = parts
            try:
                class_id = int(class_id_raw)
                athlete_id = int(athlete_id_raw)
            except ValueError:
                await query.answer("Некорректные данные.", show_alert=True)
                return
            result = await sync_to_async(
                update_attendance_status,
                thread_sensitive=True,
            )(user, class_id, athlete_id, action)
            if result == "ok":
                await query.answer("Статус обновлён")
                await _send_training_detail(query, user, class_id, origin=origin, edit=True)
            elif result == "not_found":
                await query.answer("Не найдена запись на тренировку.", show_alert=True)
            elif result == "forbidden":
                await query.answer("Этот спортсмен вам недоступен.", show_alert=True)
            elif result == "invalid":
                await query.answer("Неизвестное действие.", show_alert=True)
            else:
                await query.answer("Не удалось обновить статус.", show_alert=True)
            return

        if data == "user:plan":
            trainings = await sync_to_async(
                get_upcoming_trainings_for_user,
                thread_sensitive=True,
            )(user, limit=1)
            if not trainings:
                await query.message.reply_text("Для вашей семьи ближайших тренировок пока нет.")
                return
            summary = trainings[0]
            await _send_training_detail(query, user, summary.class_id, origin="plan")
            return

        if data == "user:family":
            state = context.user_data.pop(FAMILY_EDIT_STATE_KEY, None)
            await _clear_family_prompt_keyboard(context.bot, state)
            context.user_data.pop("family_editor_messages", None)
            await _send_family_overview(query, user)
            return

        if data.startswith("family:edit:"):
            try:
                person_id = int(data.split(":", 2)[2])
            except (ValueError, IndexError):
                await query.answer("Не удалось распознать члена семьи.", show_alert=True)
                return
            state = context.user_data.pop(FAMILY_EDIT_STATE_KEY, None)
            await _clear_family_prompt_keyboard(context.bot, state)
            context.user_data.pop(COMMENT_STATE_KEY, None)
            await _send_family_member_editor(query, user, person_id, context)
            return

        if data.startswith("family:field:"):
            parts = data.split(":", 3)
            if len(parts) != 4:
                await query.answer("Неизвестный формат данных.", show_alert=True)
                return
            _, _, person_id_raw, field_key = parts
            try:
                person_id = int(person_id_raw)
            except ValueError:
                await query.answer("Некорректный идентификатор.", show_alert=True)
                return

            config = PERSON_FIELD_CONFIG.get(field_key) or ATHLETE_FIELD_CONFIG.get(field_key)
            if not config:
                await query.answer("Поле недоступно.", show_alert=True)
                return

            existing_state = context.user_data.pop(FAMILY_EDIT_STATE_KEY, None)
            await _clear_family_prompt_keyboard(context.bot, existing_state)

            person, _, _ = await _get_member_editor_data(user, person_id)
            if not person:
                await query.answer("Не удалось найти этого члена семьи.", show_alert=True)
                return

            if config.get("model") == "athlete" and not getattr(person, "athlete", None):
                await query.answer("Для этого члена семьи нет данных спортсмена.", show_alert=True)
                return

            target = person if config.get("model") == "person" else person.athlete
            current_value = getattr(target, config["attr"], None)
            prompt = config["prompt"].format(current=_format_value_for_display(current_value, config))
            context.user_data.pop(COMMENT_STATE_KEY, None)
            instructions = [prompt]
            if config.get("allow_clear", True):
                instructions.append("Отправьте «-», чтобы очистить значение.")
            instructions.append("Или нажмите «↩️ Вернуться назад», чтобы отменить изменение.")
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("↩️ Вернуться назад", callback_data=f"family:edit:{person_id}")]]
            )
            await _clear_query_keyboard(query)
            prompt_message = await query.message.reply_text("\n".join(instructions), reply_markup=keyboard)
            context.user_data[FAMILY_EDIT_STATE_KEY] = {
                "person_id": person_id,
                "field": field_key,
                "prompt_message_id": prompt_message.message_id,
                "chat_id": prompt_message.chat_id,
            }
            await query.answer("Жду новое значение")
            return

        if data == "user:camps":
            await _clear_query_keyboard(query)
            await query.message.reply_text(
                "Скоро появится возможность просматривать план сборов и предварительно записываться. Следите за обновлениями!",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🏠 Главное меню", callback_data="user:menu")]]
                ),
            )
            return

        if data == "user:payments":
            await _clear_query_keyboard(query)
            await query.message.reply_text(
                "Раздел с данными по оплате находится в разработке. Мы сообщим, когда он станет доступен.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🏠 Главное меню", callback_data="user:menu")]]
                ),
            )
            return

    await query.message.reply_text("Действие пока недоступно.")


async def handle_family_edit_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    state = context.user_data.get(FAMILY_EDIT_STATE_KEY)
    if not state:
        return False

    message_text = update.effective_message.text
    if not message_text:
        await update.effective_message.reply_text("Пожалуйста, отправьте текстовое сообщение.")
        return True

    text = message_text.strip()
    if text.lower() in {"/cancel", "cancel", "отмена"}:
        await _clear_family_prompt_keyboard(context.bot, state)
        context.user_data.pop(FAMILY_EDIT_STATE_KEY, None)
        await update.effective_message.reply_text("Изменение отменено.")
        return True

    user = await _load_user(update.effective_user.id)
    if not user:
        await _clear_family_prompt_keyboard(context.bot, state)
        context.user_data.pop(FAMILY_EDIT_STATE_KEY, None)
        await update.effective_message.reply_text(
            "Не удалось найти ваш профиль. Нажмите /start и попробуйте снова."
        )
        return True

    success, message = await _apply_family_edit(user, state["person_id"], state["field"], message_text)
    if success:
        await _clear_family_prompt_keyboard(context.bot, state)
        context.user_data.pop(FAMILY_EDIT_STATE_KEY, None)
        await update.effective_message.reply_text(message)
        await _update_family_member_editor_card(
            context.bot,
            context,
            user,
            state["person_id"],
            update.effective_chat.id,
        )
    else:
        await update.effective_message.reply_text(message)
        lowered = message.lower()
        if "не удалось найти" in lowered or "нет данных спортсмена" in lowered:
            await _clear_family_prompt_keyboard(context.bot, state)
            context.user_data.pop(FAMILY_EDIT_STATE_KEY, None)

    return True


async def handle_comment_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not context.user_data.get(COMMENT_STATE_KEY):
        return False

    comment = (update.effective_message.text or "").strip()
    if not comment:
        await update.effective_message.reply_text("Пожалуйста, отправьте текстовый комментарий.")
        return True

    user = await sync_to_async(
        User.objects.select_related("link").filter(tg_id=update.effective_user.id).first,
        thread_sensitive=True,
    )()
    if not user:
        await update.effective_message.reply_text("Не удалось найти ваш профиль. Нажмите /start и попробуйте снова.")
        context.user_data.pop(COMMENT_STATE_KEY, None)
        return True

    await update_user_comment_async(user, comment)
    notify_pending_user_task.delay(user.id, reason="пользователь обновил комментарий")
    context.user_data.pop(COMMENT_STATE_KEY, None)
    await update.effective_message.reply_text("Комментарий передан администратору.")
    logger.info("Пользователь %s оставил комментарий для администратора", user.id)
    return True


async def handle_main_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    message = update.effective_message
    text = (message.text or "").strip().lower()
    if text not in {"начать работу", "главное меню"}:
        return False

    state = context.user_data.pop(FAMILY_EDIT_STATE_KEY, None)
    await _clear_family_prompt_keyboard(context.bot, state)
    context.user_data.pop("family_editor_messages", None)

    user = await _load_user(update.effective_user.id)
    if not user:
        await message.reply_text("Не удалось найти ваш профиль. Нажмите /start и попробуйте снова.")
        return True

    await _send_main_menu(context.bot, update.effective_chat.id, user)
    return True
