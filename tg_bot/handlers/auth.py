import logging
from typing import Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import ContextTypes

from tg_bot.services.notifications import (
    notify_admins_context,
    user_is_admin,
    user_is_coach,
    user_is_manager,
)
from tg_bot.services.user_sync import ensure_user_for_start_async
from tg_bot.services.training_overview import (
    build_attendance_lines,
    build_training_brief_lines,
    get_upcoming_trainings_for_user,
)
from users.models import User, UserPersonLinkStatus
from users.tasks import notify_pending_user_task, schedule_pending_user_notifications

logger = logging.getLogger(__name__)


def _format_login_instructions(token: Optional[str]) -> str:
    base_url = settings.SITE_BASE_URL.rstrip("/")
    login_page = f"{base_url}/"
    instructions = [
        # "Чтобы войти на сайт, откройте страницу авторизации и нажмите «Войти через Telegram».",
        # f"Ссылка для входа: {login_page}",
    ]
    if token:
        callback_url = f"{base_url}/telegram-callback/{token}/"
        instructions.append(
            "Для завершения авторизации на сайте перейдите по "
            f'<a href="{callback_url}">ссылке</a>'
        )
    return "\n".join(instructions)


def build_authenticated_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("ℹ️ Ближайшая тренировка", callback_data="user:plan")],
            [InlineKeyboardButton("📝 Сообщить о планах семьи", callback_data="user:plan")],
            [InlineKeyboardButton("📅 Расписание на неделю", callback_data="user:schedule")],
            [InlineKeyboardButton("🏕 План по сборам (в разработке)", callback_data="user:camps")],
            [InlineKeyboardButton("👪 Моя семья", callback_data="user:family")],
            [InlineKeyboardButton("💳 Данные по оплате (в разработке)", callback_data="user:payments")],
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    token = context.args[0] if context.args else None
    tg_user = update.effective_user

    logger.info(
        "Получена команда /start от tg_id=%s (token=%s)",
        tg_user.id,
        token[-6:] if token else "нет",
    )
    try:
        user, created, link = await ensure_user_for_start_async(tg_user, token)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка при выполнении /start для tg_id=%s", tg_user.id)
        await notify_admins_context(
            context,
            f"❗️ Ошибка /start для {tg_user.id}: {exc}",
        )
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                "Произошла ошибка при обработке запроса. Сообщите администратору.\n"
                f"Текст ошибки: {exc}"
            ),
        )
        return

    is_admin = await user_is_admin(tg_user.id)
    is_coach = await user_is_coach(tg_user.id)
    is_manager = await user_is_manager(tg_user.id)

    if created and not (is_admin or is_coach or is_manager):
        baseline = link.updated_at.timestamp() if link and link.updated_at else None
        schedule_pending_user_notifications(user.id, baseline=baseline)

    greeting_name = user.tg_first_name or user.first_name or "друг"
    lines = [f"Привет, {greeting_name}!"]
    reply_markup = None

    if token and user.person_id and link.status == UserPersonLinkStatus.APPROVED:
        lines.append(_format_login_instructions(token))
    elif is_admin or is_coach or is_manager:
        roles = []
        if is_admin:
            roles.append("администратор")
        if is_manager:
            roles.append("менеджер")
        if is_coach:
            roles.append("тренер")
        role_text = ", ".join(roles)
        lines.append(f"Вы вошли как {role_text}.")
        lines.append("Используйте /adminpanel или /coach для работы.")
    elif user.person_id and link.status == UserPersonLinkStatus.APPROVED:
        upcoming = await sync_to_async(
            get_upcoming_trainings_for_user,
            thread_sensitive=True,
        )(user, limit=1)

        if upcoming:
            summary = upcoming[0]
            lines.append("Ближайшая тренировка:")
            details_lines = build_training_brief_lines(summary) + [""] + build_attendance_lines(summary)
            lines.append("\n".join(line for line in details_lines if line))
        else:
            lines.append("Ближайшие тренировки пока не запланированы.")
        lines.append("Нажмите кнопку «ℹ️ Ближайшая тренировка», чтобы получить подробности.")
        lines.append(_format_login_instructions(token))
        lines.append("Вам также доступны следующие действия:")
        reply_markup = build_authenticated_keyboard()
    else:
        if link.status == UserPersonLinkStatus.REJECTED:
            status_line = "Администратор пока не предоставил вам доступ."
        else:
            status_line = "Ваш статус: на одобрении у администратора."

        lines.append(status_line)
        if link.suggested_person_id:
            suggested = link.suggested_person
            lines.append(
                "Мы предполагаем, что вы член клуба "
                f"{suggested.surname} {suggested.name}. Администратор подтвердит эту информацию."
            )
        lines.append(
            "Вы можете дополнить информацию о себе — так администратор быстрее определит,"
            " являетесь ли вы членом нашего клуба."
        )
        reply_markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Оставить комментарий администратору", callback_data="user:comment")],
            ]
        )
        if link.status == UserPersonLinkStatus.REJECTED:
            notify_pending_user_task.delay(user.id, reason="пользователь повторно запросил доступ")

    await update.effective_message.reply_html(
        "\n\n".join(lines),
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Нажмите кнопку «Начать работу» внизу, чтобы быстро открыть меню.",
        reply_markup=ReplyKeyboardMarkup([["Начать работу"]], resize_keyboard=True),
    )


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="Укажите id занятия"
        )
        logger.warning("Команда /register без аргументов от tg_id=%s", update.effective_user.id)
        return

    class_id = context.args[0]
    from school.models import Class, Person, Athlete, ClassEnrollment

    tg_id = update.effective_user.id
    user = await sync_to_async(User.objects.filter(tg_id=tg_id).first)()

    if not user:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="Сначала выполните /start"
        )
        logger.warning("Попытка /register без связанного пользователя tg_id=%s", tg_id)
        return

    person, _ = await sync_to_async(Person.objects.get_or_create)(
        surname=user.tg_last_name or user.tg_first_name,
        name=user.tg_first_name,
        defaults={"gender": "male"},
    )
    athlete, _ = await sync_to_async(Athlete.objects.get_or_create)(
        person=person,
        defaults={"level": Athlete.LEVEL_CHOICES[0][0]},
    )
    class_instance = await sync_to_async(Class.objects.get)(pk=class_id)
    await sync_to_async(ClassEnrollment.objects.get_or_create)(
        athlete=athlete, class_instance=class_instance
    )

    await context.bot.send_message(
        chat_id=update.effective_chat.id, text="Вы зарегистрированы"
    )
    logger.info("Пользователь tg_id=%s зарегистрировался на занятие %s", tg_id, class_id)


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="Укажите id занятия"
        )
        logger.warning("Команда /confirm без аргументов от tg_id=%s", update.effective_user.id)
        return

    class_id = context.args[0]
    from school.models import ClassEnrollment

    tg_id = update.effective_user.id
    user = await sync_to_async(User.objects.filter(tg_id=tg_id).first)()
    if not user:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="Сначала выполните /start"
        )
        logger.warning("Попытка /confirm без связанного пользователя tg_id=%s", tg_id)
        return

    enrollments = ClassEnrollment.objects.filter(
        class_instance_id=class_id,
        athlete__person__surname=user.tg_last_name or user.tg_first_name,
    )
    await sync_to_async(enrollments.update)(confirmed=True)
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text="Участие подтверждено"
    )
    logger.info("Пользователь tg_id=%s подтвердил участие в занятии %s", tg_id, class_id)
