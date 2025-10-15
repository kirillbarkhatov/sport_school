import logging
import os
import django
from uuid import uuid4
from asgiref.sync import sync_to_async
from httpx import request
from config.settings import BOT_TOKEN, SITE_BASE_URL
from telegram import Update, InputTextMessageContent, InlineQueryResultArticle
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    filters,
    InlineQueryHandler,
)

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Инициализация Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from users.models import User


logger = logging.getLogger("bot.telegram")


# Асинхронная команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    token = context.args[0] if context.args else None
    tg_user = update.message.from_user
    tg_id = tg_user.id
    tg_first_name = tg_user.first_name or ""
    tg_last_name = tg_user.last_name or ""
    tg_username = tg_user.username or ""

    logger.info(
        "Получена команда /start от tg_id=%s (token=%s)",
        tg_id,
        token[-6:] if token else "нет",
    )

    if not token:
        user, created = await sync_to_async(User.objects.get_or_create)(
            tg_id=tg_id,
            defaults={
                "email": f"{tg_id}@test.test",
                "tg_first_name": tg_first_name,
                "tg_last_name": tg_last_name,
                "tg_username": tg_username,
            },
        )

        if not created:
            user.tg_first_name = tg_first_name
            user.tg_last_name = tg_last_name
            user.tg_username = tg_username
        await sync_to_async(user.save)(update_fields=["tg_first_name", "tg_last_name", "tg_username"])

        logger.info("Пользователь tg_id=%s активировал бота без токена", tg_id)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                f"Привет, {tg_first_name or 'друг'}!\n"
                "Чтобы войти на сайт, откройте страницу авторизации и нажмите «Войти через Telegram»."
            ),
        )
        return

    user, _ = await sync_to_async(User.objects.get_or_create)(
        tg_id=tg_id,
        defaults={
            "email": f"{tg_id}@test.test",
            "tg_first_name": tg_first_name,
            "tg_last_name": tg_last_name,
            "tg_username": tg_username,
            "token": token,
        },
    )

    user.tg_first_name = tg_first_name
    user.tg_last_name = tg_last_name
    user.tg_username = tg_username
    user.token = token
    await sync_to_async(user.save)(
        update_fields=["tg_first_name", "tg_last_name", "tg_username", "token"]
    )

    callback_url = f"{SITE_BASE_URL}/telegram-callback/{token}/"

    logger.info(
        "Пользователь tg_id=%s получил токен входа (окончание %s)",
        tg_id,
        token[-6:],
    )

    # Сообщаем пользователю о завершении авторизации
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "Готово! Теперь откройте ссылку ниже, чтобы завершить вход на сайте:\n"
            f"{callback_url}"
        ),
    )


# Асинхронная команда /person
async def person(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Выполняем HTTP-запрос для получения данных
    api_url = f"{SITE_BASE_URL}/api/person/8/"
    logger.info("Команда /person от tg_id=%s", update.message.from_user.id)
    person_from_api = request("GET", api_url).json()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"{person_from_api['name']} {person_from_api['surname']}",
    )


# Асинхронный обработчик сообщений (эхо)
async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=update.message.text
    )


# Асинхронная команда /caps
async def caps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text_caps = " ".join(context.args).upper()
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text_caps)


async def classes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from school.models import Class

    upcoming = await sync_to_async(list)(Class.objects.order_by("date")[:5])
    if not upcoming:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Ближайших занятий не найдено",
        )
        return

    text = "\n".join(f"{cls.date:%d.%m %H:%M} {cls.group.name}" for cls in upcoming)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text)


async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="Укажите id занятия"
        )
        logger.warning("Команда /register без аргументов от tg_id=%s", update.message.from_user.id)
        return

    class_id = context.args[0]

    from school.models import Class, Person, Athlete, ClassEnrollment

    tg_id = update.message.from_user.id
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


async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="Укажите id занятия"
        )
        logger.warning("Команда /confirm без аргументов от tg_id=%s", update.message.from_user.id)
        return

    class_id = context.args[0]
    from school.models import ClassEnrollment, Class, Person

    tg_id = update.message.from_user.id
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


# Асинхронный инлайн-обработчик
async def inline_caps(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query
    if not query:
        return

    results = []
    results.append(
        InlineQueryResultArticle(
            id=str(uuid4()),
            title="Caps",
            input_message_content=InputTextMessageContent(query.upper()),
        )
    )
    await context.bot.answer_inline_query(update.inline_query.id, results)


# Обработчик неизвестных команд
async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Sorry, I didn't understand that command.",
    )


# Основной запуск бота
if __name__ == "__main__":
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    # Добавление обработчиков
    start_handler = CommandHandler("start", start)
    person_handler = CommandHandler("person", person)
    caps_handler = CommandHandler("caps", caps)
    classes_handler = CommandHandler("classes", classes)
    register_handler = CommandHandler("register", register)
    confirm_handler = CommandHandler("confirm", confirm)
    echo_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), echo)
    inline_caps_handler = InlineQueryHandler(inline_caps)

    application.add_handler(start_handler)
    application.add_handler(person_handler)
    application.add_handler(echo_handler)
    application.add_handler(caps_handler)
    application.add_handler(classes_handler)
    application.add_handler(register_handler)
    application.add_handler(confirm_handler)
    application.add_handler(inline_caps_handler)

    # Обработчик неизвестных команд
    unknown_handler = MessageHandler(filters.COMMAND, unknown)
    application.add_handler(unknown_handler)

    # Запуск бота
    application.run_polling()


# import logging
# import os
# import django
# from uuid import uuid4
#
#
# from httpx import request
# from config.settings import BOT_TOKEN
# from telegram import Update, InputTextMessageContent, InlineQueryResultArticle
# from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters, InlineQueryHandler
#
# logging.basicConfig(
#     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
#     level=logging.INFO
# )
#
# os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
# django.setup()
#
# from users.models import User
#
# async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     token = context.args[0] if context.args else None
#     if not token:
#         await context.bot.send_message(chat_id=update.effective_chat.id, text="Неверный запрос.")
#         return
#
#     try:
#         # Находим пользователя по токену
#         user = User.objects.get(token=token)
#
#         # Обновляем данные пользователя из Telegram
#         user.tg_id = update.message.from_user.id
#         # user.tg_username = update.message.from_user.username
#         user.tg_first_name = update.message.from_user.first_name
#         # user.tg_last_name = update.message.from_user.last_name
#         user.token = None  # Очищаем токен после использования
#         user.save()
#
#         await context.bot.send_message(chat_id=update.effective_chat.id, text="Вы успешно авторизованы!")
#     except User.DoesNotExist:
#         await context.bot.send_message(chat_id=update.effective_chat.id, text="Неверный или устаревший токен.")
#
#     print(update.effective_user)
#
#     await context.bot.send_message(chat_id=update.effective_chat.id, text="I'm a bot, please talk to me!")
#
#
# async def person(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     api_url = f"{SITE_BASE_URL}/api/person/8/"
#     person_from_api = request("GET", api_url).json()
#     print(person_from_api)
#     await context.bot.send_message(chat_id=update.effective_chat.id, text=f"{person_from_api["name"]} {person_from_api["surname"]}")
#
#
# async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     await context.bot.send_message(chat_id=update.effective_chat.id, text=update.message.text)
#
#
# async def caps(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     text_caps = ' '.join(context.args).upper()
#     await context.bot.send_message(chat_id=update.effective_chat.id, text=text_caps)
#
#
# async def inline_caps(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     query = update.inline_query.query
#     if not query:
#         return
#
#     results = []
#     results.append(
#         InlineQueryResultArticle(
#             id=str(uuid4()),
#             title='Caps',
#             input_message_content=InputTextMessageContent(query.upper())
#         )
#     )
#     await context.bot.answer_inline_query(update.inline_query.id, results)
#
#
# async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
#     await context.bot.send_message(chat_id=update.effective_chat.id, text="Sorry, I didn't understand that command.")
#
# if __name__ == '__main__':
#     application = ApplicationBuilder().token(BOT_TOKEN).build()
#
#     start_handler = CommandHandler('start', start)
#     person_handler = CommandHandler('person', person)
#     caps_handler = CommandHandler('caps', caps)
#     echo_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), echo)
#     inline_caps_handler = InlineQueryHandler(inline_caps)
#
#     application.add_handler(start_handler)
#     application.add_handler(person_handler)
#     application.add_handler(echo_handler)
#     application.add_handler(caps_handler)
#     application.add_handler(inline_caps_handler)
#
#     # Other handlers
#     unknown_handler = MessageHandler(filters.COMMAND, unknown)
#     application.add_handler(unknown_handler)
#
#     application.run_polling()
