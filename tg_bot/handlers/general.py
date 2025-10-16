import logging
from uuid import uuid4

from asgiref.sync import sync_to_async
from django.conf import settings
from httpx import request
from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)


async def person(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    api_url = f"{settings.SITE_BASE_URL}/api/person/8/"
    logger.info("Команда /person от tg_id=%s", update.effective_user.id)
    person_from_api = request("GET", api_url).json()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"{person_from_api['name']} {person_from_api['surname']}",
    )


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    text = message.text or ""
    print(f"echo: {text}", flush=True)
    await message.reply_text(text)


async def caps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text_caps = " ".join(context.args).upper()
    await context.bot.send_message(chat_id=update.effective_chat.id, text=text_caps)


async def classes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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


async def inline_caps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.inline_query.query
    if not query:
        return

    results = [
        InlineQueryResultArticle(
            id=str(uuid4()),
            title="Caps",
            input_message_content=InputTextMessageContent(query.upper()),
        )
    ]
    await context.bot.answer_inline_query(update.inline_query.id, results)


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text="Sorry, I didn't understand that command.",
    )
