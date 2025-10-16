from __future__ import annotations

from django.utils import timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from school.choices import ClassCoachStatus, TrainingKind, TrainingLocation
from school.models import Class

from .models import Weekday

COACH_CALLBACK_PREFIX = "coach"


def format_class_summary(class_instance: Class) -> str:
    local_dt = timezone.localtime(class_instance.date)
    weekday_label = Weekday(local_dt.weekday()).label
    lines = [
        f"{class_instance.group.name}",
        f"{weekday_label}, {local_dt:%d.%m %H:%M}",
        f"Вид: {class_instance.get_training_type_display()}",
        f"Локация: {class_instance.get_location_display()}",
    ]
    equipment = class_instance.get_equipment_display()
    if equipment:
        lines.append(f"Экипировка: {equipment}")
    lines.append(f"Статус тренера: {class_instance.get_coach_status_display()}")
    if class_instance.creation_source == "template":
        lines.append("Источник: автоматически по шаблону")
    if class_instance.comment:
        lines.append(f"Комментарий: {class_instance.comment}")
    if class_instance.coach_comment:
        lines.append(f"Комментарий тренера: {class_instance.coach_comment}")
    return "\n".join(lines)


def build_main_actions_keyboard(class_instance: Class) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    plan_button = InlineKeyboardButton(
        "✅ Запланировать",
        callback_data=f"{COACH_CALLBACK_PREFIX}:plan:{class_instance.pk}",
    )
    cancel_button = InlineKeyboardButton(
        "🚫 Отменить",
        callback_data=f"{COACH_CALLBACK_PREFIX}:cancel:{class_instance.pk}",
    )
    edit_button = InlineKeyboardButton(
        "✏️ Изменить",
        callback_data=f"{COACH_CALLBACK_PREFIX}:edit:{class_instance.pk}",
    )

    if class_instance.coach_status == ClassCoachStatus.PLANNED:
        rows.append([cancel_button, edit_button])
    elif class_instance.coach_status == ClassCoachStatus.CANCELLED:
        rows.append([plan_button])
        rows.append([edit_button])
    else:  # pending
        rows.append([plan_button, cancel_button])
        rows.append([edit_button])

    return InlineKeyboardMarkup(rows)


def build_edit_keyboard(class_instance: Class) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                "📍 Локация",
                callback_data=f"{COACH_CALLBACK_PREFIX}:list_location:{class_instance.pk}",
            )
        ],
        [
            InlineKeyboardButton(
                "🏷 Вид тренировки",
                callback_data=f"{COACH_CALLBACK_PREFIX}:list_training:{class_instance.pk}",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=f"{COACH_CALLBACK_PREFIX}:back:{class_instance.pk}",
            )
        ],
    ]
    return InlineKeyboardMarkup(rows)


def build_location_keyboard(class_instance: Class) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for value, label in TrainingLocation.choices:
        prefix = "✅ " if class_instance.location == value else ""
        rows.append(
            [
                InlineKeyboardButton(
                    f"{prefix}{label}",
                    callback_data=f"{COACH_CALLBACK_PREFIX}:set_location:{class_instance.pk}:{value}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=f"{COACH_CALLBACK_PREFIX}:edit:{class_instance.pk}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def build_training_type_keyboard(class_instance: Class) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for value, label in TrainingKind.choices:
        prefix = "✅ " if class_instance.training_type == value else ""
        rows.append(
            [
                InlineKeyboardButton(
                    f"{prefix}{label}",
                    callback_data=f"{COACH_CALLBACK_PREFIX}:set_training:{class_instance.pk}:{value}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data=f"{COACH_CALLBACK_PREFIX}:edit:{class_instance.pk}",
            )
        ]
    )
    return InlineKeyboardMarkup(rows)
