from __future__ import annotations

import logging
from datetime import timedelta
from typing import Sequence

from asgiref.sync import sync_to_async
from django.conf import settings
from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

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

logger = logging.getLogger(__name__)

COMMENT_STATE_KEY = "awaiting_comment"


async def _load_user(tg_id: int) -> User | None:
    return await sync_to_async(
        User.objects.select_related("person").filter(tg_id=tg_id).first,
        thread_sensitive=True,
    )()


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

    base_url = (settings.SITE_BASE_URL or "").rstrip("/")
    allow_url_buttons = base_url.startswith("https://")
    keyboard_rows: list[list[InlineKeyboardButton]] = []
    text_blocks: list[str] = []

    for overview in overviews:
        block_lines = [f"Семья — {overview.family_name}"]
        if allow_url_buttons:
            family_url = f"{base_url}{overview.family_edit_path}"
            keyboard_rows.append([InlineKeyboardButton(f"✏️ {overview.family_name}", url=family_url)])
        else:
            block_lines.append(f"Редактировать: {base_url}{overview.family_edit_path}")
        for member in overview.members:
            block_lines.append(f"• {member.title}")
            if member.athlete_extra:
                block_lines.append(f"  {member.athlete_extra}")
            if member.person_edit_path:
                if allow_url_buttons:
                    member_url = f"{base_url}{member.person_edit_path}"
                    keyboard_rows.append([InlineKeyboardButton(f"✏️ {member.title}", url=member_url)])
                else:
                    block_lines.append(f"  Редактировать: {base_url}{member.person_edit_path}")
        text_blocks.append("\n".join(block_lines))

    keyboard_rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="user:menu")])

    text = "\n\n".join(text_blocks)
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
        await query.message.reply_text(
            "Выберите действие:", reply_markup=build_authenticated_keyboard()
        )
        return

    if data in {"user:schedule", "schedule:list", "user:plan", "user:family"} or data.startswith(
        (
            "schedule:view:",
            "attendance:",
        )
    ) or data in {"user:camps", "user:payments", "user:family_edit"}:
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
            await _send_family_overview(query, user)
            return

        if data == "user:camps":
            await query.message.reply_text(
                "Скоро появится возможность просматривать план сборов и предварительно записываться. Следите за обновлениями!"
            )
            return

        if data == "user:payments":
            await query.message.reply_text(
                "Раздел с данными по оплате находится в разработке. Мы сообщим, когда он станет доступен."
            )
            return

        if data == "user:family_edit":
            url = settings.SITE_BASE_URL.rstrip("/") + "/members/"
            await query.message.reply_text(
                "Для обновления данных семьи воспользуйтесь веб-интерфейсом:"
                f" {url} (раздел «Члены клуба»)."
            )
            return

    await query.message.reply_text("Действие пока недоступно.")


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
