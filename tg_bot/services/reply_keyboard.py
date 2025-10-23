from __future__ import annotations

from telegram import KeyboardButton, ReplyKeyboardMarkup


def build_persistent_reply_keyboard(*, show_manager: bool, show_coach: bool) -> ReplyKeyboardMarkup:
    """Build a persistent reply keyboard tailored to the user's roles."""
    keyboard: list[list[KeyboardButton]] = [
        [KeyboardButton("Начать работу")],
    ]

    role_buttons: list[KeyboardButton] = []
    if show_manager:
        role_buttons.append(KeyboardButton("Менеджер"))
    if show_coach:
        role_buttons.append(KeyboardButton("Тренер"))

    if role_buttons:
        keyboard.append(role_buttons)

    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        is_persistent=True,
    )

