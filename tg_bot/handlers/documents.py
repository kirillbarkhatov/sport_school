from __future__ import annotations

import logging
from pathlib import Path

from django.conf import settings
from telegram import Update
from telegram.ext import ContextTypes

from bot.tasks import process_telegram_document_task

logger = logging.getLogger(__name__)


def _file_extension(filename: str) -> str:
    return Path(filename or "").suffix.lower().lstrip(".")


async def telegram_document_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not bool(getattr(settings, "TELEGRAM_DOC_IMPORT_ENABLED", False)):
        return

    message = update.effective_message
    if not message or not message.document:
        return

    chat = update.effective_chat
    if not chat:
        return

    allowed_chat_ids = set(getattr(settings, "TELEGRAM_DOC_IMPORT_CHAT_IDS", []))
    if allowed_chat_ids and chat.id not in allowed_chat_ids:
        return

    tg_document = message.document
    filename = tg_document.file_name or ""
    extension = _file_extension(filename)
    allowed_ext = set(getattr(settings, "TELEGRAM_DOC_IMPORT_ALLOWED_EXTENSIONS", ("pdf", "doc", "docx")))
    if extension not in allowed_ext:
        logger.info(
            "telegram document skipped by extension chat_id=%s message_id=%s filename=%s extension=%s",
            chat.id,
            message.message_id,
            filename,
            extension or "unknown",
        )
        return

    max_size_mb = int(getattr(settings, "TELEGRAM_DOC_IMPORT_MAX_SIZE_MB", 20))
    max_size = max_size_mb * 1024 * 1024
    if tg_document.file_size and int(tg_document.file_size) > max_size:
        logger.info(
            "telegram document skipped by size chat_id=%s message_id=%s filename=%s size=%s",
            chat.id,
            message.message_id,
            filename,
            tg_document.file_size,
        )
        return

    process_telegram_document_task.delay(
        chat_id=int(chat.id),
        message_id=int(message.message_id),
        telegram_file_id=tg_document.file_id,
        telegram_file_unique_id=tg_document.file_unique_id,
        filename=filename,
        mime_type=tg_document.mime_type or "",
        file_size=int(tg_document.file_size or 0),
        sender_tg_id=int(update.effective_user.id) if update.effective_user else None,
    )
    logger.info(
        "telegram document queued chat_id=%s message_id=%s filename=%s",
        chat.id,
        message.message_id,
        filename,
    )
