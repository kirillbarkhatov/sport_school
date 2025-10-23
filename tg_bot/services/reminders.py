from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from tg_bot.services.training_overview import (
    TrainingSummary,
    build_attendance_lines,
    build_training_brief_lines,
)


def build_training_reminder_text(summary: TrainingSummary, *, reminders_enabled: bool) -> str:
    lines: list[str] = ["🛎 Напоминание о сегодняшней тренировке", ""]
    lines.extend(build_training_brief_lines(summary))
    lines.append("")
    lines.extend(build_attendance_lines(summary))
    lines.append("")
    if reminders_enabled:
        lines.append(
            "Если планы изменились, обновите статус участия или отключите напоминания ниже."
        )
    else:
        lines.append(
            "Напоминания сейчас выключены. Вы можете включить их обратно ниже или через главное меню."
        )
    return "\n".join(lines)


def build_training_reminder_markup(
    summary: TrainingSummary,
    *,
    reminders_enabled: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                "✏️ Обновить статус",
                callback_data=f"schedule:view:{summary.class_id}",
            )
        ]
    ]
    if reminders_enabled:
        rows.append(
            [
                InlineKeyboardButton(
                    "🔕 Отключить напоминания",
                    callback_data=f"reminder:disable:{summary.class_id}",
                )
            ]
        )
    else:
        rows.append(
            [
                InlineKeyboardButton(
                    "🔔 Включить напоминания",
                    callback_data=f"reminder:enable:{summary.class_id}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("⚙️ Настройки", callback_data="user:reminders")])
    rows.append([InlineKeyboardButton("🏠 Главное меню", callback_data="user:menu")])
    return InlineKeyboardMarkup(rows)
