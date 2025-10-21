import logging
from datetime import timedelta
from typing import Optional

from asgiref.sync import sync_to_async
from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from classes.telegram import (
    COACH_CALLBACK_PREFIX,
    build_edit_keyboard,
    build_location_keyboard,
    build_main_actions_keyboard,
    build_training_type_keyboard,
    format_class_summary,
)
from school.choices import ClassCoachStatus, TrainingKind, TrainingLocation
from school.models import Class
from tg_bot.services.notifications import user_is_coach

logger = logging.getLogger(__name__)


async def _ensure_coach(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if await user_is_coach(update.effective_user.id):
        return True
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="У вас нет прав тренера для выполнения этого действия.",
    )
    return False


async def coach_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _ensure_coach(update, context):
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Сегодня", callback_data=f"{COACH_CALLBACK_PREFIX}:list:today"
                ),
                InlineKeyboardButton(
                    "Завтра", callback_data=f"{COACH_CALLBACK_PREFIX}:list:tomorrow"
                ),
            ],
            [
                InlineKeyboardButton(
                    "Необработанные (7 дней)",
                    callback_data=f"{COACH_CALLBACK_PREFIX}:list:pending",
                )
            ],
        ]
    )

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Выберите какой список занятий показать:",
        reply_markup=keyboard,
    )


async def _fetch_classes(scope: str) -> list[Class]:
    today = timezone.localdate()

    qs = Class.objects.select_related("group").order_by("date")

    if scope == "today":
        return await sync_to_async(list)(
            qs.filter(date__date=today, coach_status__in=(ClassCoachStatus.PENDING, ClassCoachStatus.PLANNED))
        )
    if scope == "tomorrow":
        target = today + timedelta(days=1)
        return await sync_to_async(list)(
            qs.filter(date__date=target, coach_status__in=(ClassCoachStatus.PENDING, ClassCoachStatus.PLANNED))
        )
    if scope == "pending":
        limit = today + timedelta(days=7)
        return await sync_to_async(list)(
            qs.filter(
                coach_status=ClassCoachStatus.PENDING,
                date__date__gte=today,
                date__date__lte=limit,
            )
        )
    return []


async def _load_class(class_id: int) -> Optional[Class]:
    return await sync_to_async(Class.objects.select_related("group").filter(pk=class_id).first)()


async def handle_coach_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()

    if not await user_is_coach(update.effective_user.id):
        await query.edit_message_text("У вас нет прав тренера.")
        return

    data = query.data or ""
    if not data.startswith(f"{COACH_CALLBACK_PREFIX}:"):
        await query.answer("Неизвестное действие", show_alert=True)
        return

    parts = data.split(":")
    # Expected formats:
    # coach:list:scope
    # coach:plan:<id>
    # coach:cancel:<id>
    # coach:edit:<id>
    # coach:list_location:<id>
    # coach:list_training:<id>
    # coach:set_location:<id>:<value>
    # coach:set_training:<id>:<value>
    # coach:back:<id>

    if len(parts) < 3:
        await query.answer("Некорректный запрос", show_alert=True)
        return

    action = parts[1]

    if action == "list":
        scope = parts[2]
        classes = await _fetch_classes(scope)
        if not classes:
            await query.edit_message_text("Подходящих занятий не найдено.")
            return
        for class_instance in classes:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=format_class_summary(class_instance),
                reply_markup=build_main_actions_keyboard(class_instance),
            )
        await query.delete_message()
        return

    try:
        class_id = int(parts[2])
    except ValueError:
        await query.answer("Некорректный идентификатор", show_alert=True)
        return

    class_instance = await _load_class(class_id)
    if not class_instance:
        await query.answer("Занятие не найдено", show_alert=True)
        await query.edit_message_text("Занятие не найдено или было удалено.")
        return

    if action == "plan":
        class_instance.coach_status = ClassCoachStatus.PLANNED
        class_instance.coach_status_set_at = timezone.now()
        await sync_to_async(class_instance.save)(update_fields=["coach_status", "coach_status_set_at"])
        await query.edit_message_text(
            format_class_summary(class_instance),
            reply_markup=build_main_actions_keyboard(class_instance),
        )
        await query.answer("Занятие отмечено как запланированное")
        return

    if action == "cancel":
        class_instance.coach_status = ClassCoachStatus.CANCELLED
        class_instance.coach_status_set_at = timezone.now()
        await sync_to_async(class_instance.save)(update_fields=["coach_status", "coach_status_set_at"])
        await query.edit_message_text(
            format_class_summary(class_instance),
            reply_markup=build_main_actions_keyboard(class_instance),
        )
        await query.answer("Занятие отменено")
        return

    if action == "back":
        await query.edit_message_text(
            format_class_summary(class_instance),
            reply_markup=build_main_actions_keyboard(class_instance),
        )
        return

    if action == "edit":
        await query.edit_message_text(
            format_class_summary(class_instance),
            reply_markup=build_edit_keyboard(class_instance),
        )
        return

    if action == "list_location":
        await query.edit_message_text(
            format_class_summary(class_instance) + "\n\nВыберите локацию:",
            reply_markup=build_location_keyboard(class_instance),
        )
        return

    if action == "list_training":
        await query.edit_message_text(
            format_class_summary(class_instance) + "\n\nВыберите вид тренировки:",
            reply_markup=build_training_type_keyboard(class_instance),
        )
        return

    if action == "set_location" and len(parts) == 4:
        new_value = parts[3]
        valid_locations = {value for value, _ in TrainingLocation.choices}
        if new_value not in valid_locations:
            await query.answer("Некорректная локация", show_alert=True)
            return
        class_instance.location = new_value
        update_fields = ["location"]
        if class_instance.coach_status == ClassCoachStatus.PLANNED:
            class_instance.coach_status_set_at = timezone.now()
            update_fields.append("coach_status_set_at")
        await sync_to_async(class_instance.save)(update_fields=update_fields)
        await query.edit_message_text(
            format_class_summary(class_instance),
            reply_markup=build_edit_keyboard(class_instance),
        )
        await query.answer("Локация обновлена")
        return

    if action == "set_training" and len(parts) == 4:
        new_value = parts[3]
        valid_kinds = {value for value, _ in TrainingKind.choices}
        if new_value not in valid_kinds:
            await query.answer("Некорректный тип", show_alert=True)
            return
        class_instance.training_type = new_value
        update_fields = ["training_type"]
        if class_instance.coach_status == ClassCoachStatus.PLANNED:
            class_instance.coach_status_set_at = timezone.now()
            update_fields.append("coach_status_set_at")
        await sync_to_async(class_instance.save)(update_fields=update_fields)
        await query.edit_message_text(
            format_class_summary(class_instance),
            reply_markup=build_edit_keyboard(class_instance),
        )
        await query.answer("Тип тренировки обновлён")
        return

    await query.answer("Неизвестное действие", show_alert=True)
