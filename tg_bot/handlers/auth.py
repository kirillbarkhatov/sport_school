import logging
from typing import Optional

from asgiref.sync import sync_to_async
from django.conf import settings
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from tg_bot.services.notifications import notify_admins_context
from users.models import User

logger = logging.getLogger(__name__)


def _format_login_instructions(token: Optional[str]) -> str:
    base_url = settings.SITE_BASE_URL.rstrip("/")
    login_page = f"{base_url}/"
    instructions = [
        "Чтобы войти на сайт, откройте страницу авторизации и нажмите «Войти через Telegram».",
        f"Ссылка для входа: {login_page}",
    ]
    if token:
        callback_url = f"{base_url}/telegram-callback/{token}/"
        instructions.append(
            "Либо нажмите прямо сейчас: "
            f'<a href="{callback_url}">Подтвердить авторизацию</a>'
        )
    return "\n".join(instructions)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    token = context.args[0] if context.args else None
    tg_user = update.effective_user

    logger.info(
        "Получена команда /start от tg_id=%s (token=%s)",
        tg_user.id,
        token[-6:] if token else "нет",
    )
    await notify_admins_context(
        context,
        "🔔 /start\n"
        f"id={tg_user.id}\n"
        f"username=@{tg_user.username or '-'}\n"
        f"name: {tg_user.first_name or ''} {tg_user.last_name or ''}\n"
        f"token={token or '-'}",
    )

    response = [
        f"Привет, {tg_user.first_name or 'друг'}!",
        _format_login_instructions(token),
    ]
    await update.effective_message.reply_html(
        "\n".join(response),
        disable_web_page_preview=True,
    )
    logger.info("Приветственное сообщение отправлено пользователю %s", tg_user.id)

    try:
        defaults = {
            "email": f"{tg_user.id}@autogen.local",
            "tg_first_name": tg_user.first_name or "",
            "tg_last_name": tg_user.last_name or "",
            "tg_username": tg_user.username or "",
        }
        if token:
            defaults["token"] = token

        user, created = await sync_to_async(User.objects.get_or_create)(
            tg_id=tg_user.id,
            defaults=defaults,
        )

        user.tg_first_name = tg_user.first_name or ""
        user.tg_last_name = tg_user.last_name or ""
        user.tg_username = tg_user.username or ""
        if token:
            user.token = token
        await sync_to_async(user.save)(
            update_fields=["tg_first_name", "tg_last_name", "tg_username", "token"]
        )

        await notify_admins_context(
            context,
            f"✅ Пользователь tg_id={tg_user.id} сохранён. created={created}",
        )
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
