from __future__ import annotations

import logging
from pathlib import PurePosixPath

import requests
from celery import shared_task
from django.conf import settings
from django.core.files.base import ContentFile

from school.document_ingest import (
    DocumentIngestValidationError,
    ingest_single_document,
)

logger = logging.getLogger(__name__)


def _telegram_api_timeout_sec() -> float:
    return float(getattr(settings, "TELEGRAM_DOC_IMPORT_HTTP_TIMEOUT_SEC", 30))


def _get_telegram_file_path(*, token: str, file_id: str) -> str:
    response = requests.get(
        f"https://api.telegram.org/bot{token}/getFile",
        params={"file_id": file_id},
        timeout=_telegram_api_timeout_sec(),
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"telegram getFile failed: {payload}")
    result = payload.get("result") or {}
    file_path = result.get("file_path") or ""
    if not file_path:
        raise RuntimeError(f"telegram getFile returned empty file_path for file_id={file_id}")
    return file_path


def _download_telegram_file(*, token: str, file_path: str) -> bytes:
    response = requests.get(
        f"https://api.telegram.org/file/bot{token}/{file_path}",
        timeout=_telegram_api_timeout_sec(),
    )
    response.raise_for_status()
    return response.content


@shared_task(bind=True, max_retries=2, default_retry_delay=10)
def process_telegram_document_task(
    self,
    *,
    chat_id: int,
    message_id: int,
    telegram_file_id: str,
    telegram_file_unique_id: str,
    filename: str,
    mime_type: str | None = "",
    file_size: int | None = 0,
    sender_tg_id: int | None = None,
) -> dict:
    token = getattr(settings, "BOT_TOKEN", "")
    if not token:
        raise RuntimeError("BOT_TOKEN is not configured")

    try:
        file_path = _get_telegram_file_path(token=token, file_id=telegram_file_id)
        content = _download_telegram_file(token=token, file_path=file_path)
    except requests.RequestException as exc:
        logger.warning(
            "telegram document fetch failed chat_id=%s message_id=%s file_id=%s error=%s",
            chat_id,
            message_id,
            telegram_file_id,
            exc,
        )
        raise self.retry(exc=exc)

    resolved_name = (filename or "").strip() or PurePosixPath(file_path).name or f"tg-{telegram_file_unique_id}"
    resolved_size = int(file_size or 0) or len(content)
    django_file = ContentFile(content, name=resolved_name)

    try:
        result = ingest_single_document(
            file_obj=django_file,
            original_name=resolved_name,
            mime_type=mime_type or "",
            size=resolved_size,
            uploaded_by=None,
            source="telegram",
            source_meta={
                "chat_id": int(chat_id),
                "message_id": int(message_id),
                "sender_tg_id": int(sender_tg_id) if sender_tg_id else None,
                "file_id": telegram_file_id,
                "file_unique_id": telegram_file_unique_id,
            },
            enqueue_analysis=True,
            deduplicate=bool(getattr(settings, "TELEGRAM_DOC_IMPORT_DEDUPLICATE", True)),
            enqueue_existing=False,
        )
    except DocumentIngestValidationError as exc:
        logger.info(
            "telegram document skipped chat_id=%s message_id=%s reason=%s filename=%s",
            chat_id,
            message_id,
            exc,
            resolved_name,
        )
        return {
            "ok": False,
            "skipped": True,
            "reason": str(exc),
            "chat_id": chat_id,
            "message_id": message_id,
            "filename": resolved_name,
        }

    logger.info(
        "telegram document imported chat_id=%s message_id=%s document_id=%s created=%s reused=%s enqueued=%s",
        chat_id,
        message_id,
        result.document_id,
        result.created,
        result.reused,
        result.analysis_enqueued,
    )
    return {
        "ok": True,
        "chat_id": chat_id,
        "message_id": message_id,
        "document_id": result.document_id,
        "created": result.created,
        "reused": result.reused,
        "analysis_enqueued": result.analysis_enqueued,
    }
