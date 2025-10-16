import logging
import os
import sys
from pathlib import Path

import django
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    ChatMemberHandler,
    CommandHandler,
    InlineQueryHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from config.settings import BOT_TOKEN  # noqa: E402

# Настройка логирования
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Инициализация Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from tg_bot.handlers.admin import approve, admin_panel, handle_admin_callback  # noqa: E402
from tg_bot.handlers.auth import confirm, register, start  # noqa: E402
from tg_bot.handlers.general import (  # noqa: E402
    caps,
    classes,
    echo,
    inline_caps,
    person,
    unknown,
)
from tg_bot.services.audience import (  # noqa: E402
    handle_chat_member_update,
    track_audience,
)


def build_application():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(TypeHandler(Update, track_audience), group=0)
    application.add_handler(
        ChatMemberHandler(
            handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER
        ),
        group=0,
    )
    application.add_handler(
        ChatMemberHandler(
            handle_chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER
        ),
        group=0,
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("person", person))
    application.add_handler(CommandHandler("caps", caps))
    application.add_handler(CommandHandler("classes", classes))
    application.add_handler(CommandHandler("register", register))
    application.add_handler(CommandHandler("confirm", confirm))
    application.add_handler(CommandHandler("approve", approve))
    application.add_handler(CommandHandler("adminpanel", admin_panel))

    application.add_handler(CallbackQueryHandler(handle_admin_callback, pattern=r"^admin:"))

    application.add_handler(InlineQueryHandler(inline_caps))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), echo))
    application.add_handler(MessageHandler(filters.COMMAND, unknown))

    return application


if __name__ == "__main__":
    build_application().run_polling()


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
