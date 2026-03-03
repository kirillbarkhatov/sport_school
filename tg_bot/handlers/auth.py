import logging
from typing import Optional, Tuple
from urllib.parse import quote_plus

from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from tg_bot.services.notifications import (
    notify_admins_context,
    user_is_admin,
    user_is_coach,
    user_is_manager,
)
from tg_bot.handlers.onboarding import maybe_start_group_onboarding
from tg_bot.services.user_sync import ensure_user_for_start_async
from tg_bot.services.training_overview import (
    build_attendance_lines,
    build_training_brief_lines,
    get_upcoming_trainings_for_user,
)
from tg_bot.services.reply_keyboard import build_persistent_reply_keyboard
from users.models import User, UserPersonLinkStatus
from users.tasks import notify_pending_user_task, schedule_pending_user_notifications

logger = logging.getLogger(__name__)

START_KIND_AUTH = "auth"
START_KIND_COMPETITION = "comp"
START_KIND_DEFAULT = "default"


def _parse_start_payload(raw: Optional[str]) -> Tuple[str, Optional[str], Optional[int]]:
    """Return (kind, token, competition_id). Accepts legacy ':' and new '_' delimiters."""
    if not raw:
        return START_KIND_DEFAULT, None, None

    # New format: comp_<id>_<token>
    if raw.startswith(f"{START_KIND_COMPETITION}_"):
        parts = raw.split("_", 2)
        if len(parts) >= 3:
            comp_part = parts[1]
            token_part = parts[2]
            try:
                comp_id = int(comp_part)
            except ValueError:
                comp_id = None
            return START_KIND_COMPETITION, token_part, comp_id

    # Legacy format: comp:<id>:<token>
    if raw.startswith(f"{START_KIND_COMPETITION}:"):
        parts = raw.split(":", 2)
        if len(parts) == 3:
            comp_part = parts[1]
            token_part = parts[2]
            try:
                comp_id = int(comp_part)
            except ValueError:
                comp_id = None
            return START_KIND_COMPETITION, token_part, comp_id

    # New format: auth_<token>
    if raw.startswith(f"{START_KIND_AUTH}_"):
        token_part = raw.split("_", 1)[1] if "_" in raw else None
        return START_KIND_AUTH, token_part, None

    # Legacy format: auth:<token>
    if raw.startswith(f"{START_KIND_AUTH}:"):
        token_part = raw.split(":", 1)[1] if ":" in raw else None
        return START_KIND_AUTH, token_part, None

    return START_KIND_DEFAULT, raw, None


def _build_competition_apply_url(comp_id: int) -> Optional[str]:
    if not comp_id:
        return None
    try:
        from django.urls import reverse
        from school.models import CompetitionApplicationLink

        link = CompetitionApplicationLink.objects.filter(
            competition_id=comp_id,
            is_active=True,
        ).first()
        if not link:
            return None
        path = reverse("school:competition_apply", kwargs={"pk": comp_id, "token": link.token})
        return f"{settings.SITE_BASE_URL.rstrip('/')}{path}"
    except Exception:  # noqa: BLE001
        logger.exception("Не удалось построить ссылку на заявку для соревнования %s", comp_id)
        return None


def _format_login_instructions(token: Optional[str], next_url: Optional[str] = None) -> str:
    base_url = settings.SITE_BASE_URL.rstrip("/")
    instructions = [
        # "Чтобы войти на сайт, откройте страницу авторизации и нажмите «Войти через Telegram».",
        # f"Ссылка для входа: {login_page}",
    ]
    if token:
        callback_url = f"{base_url}/telegram-callback/{token}/"
        if next_url:
            callback_url = f"{callback_url}?next={quote_plus(next_url)}"
        instructions.append(
            "🔐 Для завершения авторизации на сайте перейдите по "
            f'<a href="{callback_url}">ссылке</a>'
        )
    return "\n".join(instructions)


def build_authenticated_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("ℹ️ Ближайшая тренировка", callback_data="user:plan")],
            # [InlineKeyboardButton("📝 Сообщить о планах семьи", callback_data="user:plan")],
            [InlineKeyboardButton("📅 Расписание на неделю", callback_data="user:schedule")],
            [InlineKeyboardButton("🛎 Напоминания", callback_data="user:reminders")],
            [InlineKeyboardButton("🏕 План по сборам (в разработке)", callback_data="user:camps")],
            [InlineKeyboardButton("👪 Моя семья", callback_data="user:family")],
            [InlineKeyboardButton("💳 Данные по оплате (в разработке)", callback_data="user:payments")],
        ]
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw_arg = context.args[0] if context.args else None
    start_kind, token, comp_id = _parse_start_payload(raw_arg)
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
    is_privileged = is_admin or is_coach or is_manager
    link_status = link.status if link else None
    is_confirmed = bool(user.person_id and link_status == UserPersonLinkStatus.APPROVED)
    can_auto_bind = bool(
        is_confirmed
        or (
            link
            and link.suggested_person_id
            and "telegram_id_exact" in (link.matched_reasons or [])
        )
    )

    should_try_group_onboarding = start_kind != START_KIND_COMPETITION
    if should_try_group_onboarding and await maybe_start_group_onboarding(
        update,
        context,
        user=user,
        is_privileged=is_privileged,
        can_auto_bind=can_auto_bind,
    ):
        return

    if start_kind == START_KIND_COMPETITION:
        apply_url = await sync_to_async(_build_competition_apply_url, thread_sensitive=True)(comp_id)
        callback_url = f"{settings.SITE_BASE_URL.rstrip('/')}/telegram-callback/{token}/"
        if apply_url:
            callback_url = f"{callback_url}?next={quote_plus(apply_url)}"
        text = "Перейдите по ссылке, чтобы продолжить работу с заявкой."
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Продолжить заявку", url=callback_url)]]
        )
        await update.effective_message.reply_html(
            text,
            disable_web_page_preview=True,
            reply_markup=markup,
        )
        return

    if start_kind == START_KIND_AUTH:
        callback_url = f"{settings.SITE_BASE_URL.rstrip('/')}/telegram-callback/{token}/"
        text = "Перейдите по ссылке, чтобы авторизоваться на сайте."
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Авторизоваться", url=callback_url)]]
        )
        await update.effective_message.reply_html(
            text,
            disable_web_page_preview=True,
            reply_markup=markup,
        )
        return

    if created and not (is_admin or is_coach or is_manager):
        baseline = link.updated_at.timestamp() if link and link.updated_at else None
        schedule_pending_user_notifications(user.id, baseline=baseline)

    greeting_name = user.tg_first_name or user.first_name or "друг"
    lines = [f"👋 Привет, {greeting_name}!"]
    reply_markup = None
    show_quick_commands = is_admin or is_coach or is_manager or is_confirmed
    login_instructions = _format_login_instructions(token)

    # Ограниченный доступ к функциям бота
    if not is_privileged and not user.is_bot_full_access:
        lines = [f"👋 Привет, {greeting_name}!"]
        if is_confirmed:
            lines.append("Ваш доступ в боте будет открыт администратором. Пока доступны авторизация и заявки.")
        else:
            lines.append("Вы находитесь на одобрении. Мы уведомим, когда доступ будет открыт.")
        if login_instructions:
            lines.append(login_instructions)
        await update.effective_message.reply_html(
            "\n\n".join(lines),
            disable_web_page_preview=True,
        )
        return

    if token and is_confirmed and login_instructions:
        lines.append(login_instructions)
    elif is_admin or is_coach or is_manager:
        roles = []
        if is_admin:
            roles.append("администратор")
        if is_manager:
            roles.append("менеджер")
        if is_coach:
            roles.append("тренер")
        role_text = ", ".join(roles)
        lines.append(f"🛠️ Вы вошли как {role_text}.")
        # lines.append("⚙️ Используйте /adminpanel или /coach для работы.")
    elif is_confirmed:
        upcoming = await sync_to_async(
            get_upcoming_trainings_for_user,
            thread_sensitive=True,
        )(user, limit=1)

        if upcoming:
            summary = upcoming[0]
            lines.append("🏋️ Ближайшая тренировка:")
            details_lines = build_training_brief_lines(summary) + [""] + build_attendance_lines(summary)
            lines.append("\n".join(line for line in details_lines if line))
        else:
            lines.append("📭 Ближайшие тренировки пока не запланированы.")
        lines.append("ℹ️ Нажмите кнопку «ℹ️ Ближайшая тренировка», чтобы получить подробности.")
        if login_instructions:
            lines.append(login_instructions)
        lines.append("🧭 Вам также доступны следующие действия:")
        reply_markup = build_authenticated_keyboard()
    else:
        if link_status == UserPersonLinkStatus.REJECTED:
            status_line = "🚫 Администратор пока не предоставил вам доступ."
        else:
            status_line = "⏳ Ваш статус: на одобрении у администратора."

        lines.append(status_line)
        if link and link.suggested_person_id:
            suggested = link.suggested_person
            lines.append(
                "🔎 Мы предполагаем, что вы член клуба "
                f"{suggested.surname} {suggested.name}. Администратор подтвердит эту информацию."
            )
        lines.append(
            "📝 Вы можете дополнить информацию о себе — так администратор быстрее определит,"
            " являетесь ли вы членом нашего клуба."
        )
        reply_markup = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("💬 Оставить комментарий администратору", callback_data="user:comment")],
            ]
        )
        if link_status == UserPersonLinkStatus.REJECTED:
            notify_pending_user_task.delay(user.id, reason="пользователь повторно запросил доступ")

    await update.effective_message.reply_html(
        "\n\n".join(lines),
        disable_web_page_preview=True,
        reply_markup=reply_markup,
    )

    if show_quick_commands:
        quick_commands_markup = build_persistent_reply_keyboard(
            show_manager=is_admin or is_manager,
            show_coach=is_coach,
        )

        prompt_lines = ["⬇️ Внизу доступно меню быстрых команд."]
        if is_admin or is_manager or is_coach:
            role_hints: list[str] = []
            if is_admin or is_manager:
                role_hints.append("«Менеджер»")
            if is_coach:
                role_hints.append("«Тренер»")
            if role_hints:
                prompt_lines.append(
                    f"⚙️ Используйте {', '.join(role_hints)} для перехода в рабочие панели."
                )
        prompt_lines.append("🔁 Кнопка «Начать работу» откроет главное меню.")

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="\n".join(prompt_lines),
            reply_markup=quick_commands_markup,
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
