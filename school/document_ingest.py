from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from django.conf import settings

from school.models import Document
from school.tasks import enqueue_documents_for_ai_analysis

logger = logging.getLogger(__name__)


class DocumentIngestValidationError(ValueError):
    """Raised when input file does not match ingest constraints."""


@dataclass(slots=True)
class IngestResult:
    document_id: int
    created: bool
    reused: bool
    analysis_enqueued: bool
    skip_reason: str | None = None


def _normalize_extensions(raw_extensions: list[str] | tuple[str, ...] | set[str] | None) -> set[str]:
    if not raw_extensions:
        return {"pdf", "doc", "docx"}
    normalized: set[str] = set()
    for item in raw_extensions:
        value = str(item).strip().lower().lstrip(".")
        if value:
            normalized.add(value)
    return normalized or {"pdf", "doc", "docx"}


def _guess_extension(filename: str) -> str:
    suffix = Path(filename or "").suffix.lower().lstrip(".")
    return suffix


def _build_content_hash(file_obj: Any) -> str:
    sha256 = hashlib.sha256()
    if hasattr(file_obj, "chunks"):
        for chunk in file_obj.chunks():
            sha256.update(chunk)
    else:
        file_obj.seek(0)
        while True:
            chunk = file_obj.read(1024 * 1024)
            if not chunk:
                break
            sha256.update(chunk)
    file_obj.seek(0)
    return sha256.hexdigest()


def ingest_single_document(
    *,
    file_obj: Any,
    original_name: str,
    mime_type: str,
    size: int,
    uploaded_by=None,
    source: str = "",
    source_meta: dict[str, Any] | None = None,
    enqueue_analysis: bool = True,
    deduplicate: bool = True,
    enqueue_existing: bool = False,
    allowed_extensions: list[str] | tuple[str, ...] | set[str] | None = None,
    max_size_mb: int | None = None,
) -> IngestResult:
    allowed_ext = _normalize_extensions(
        allowed_extensions or getattr(settings, "TELEGRAM_DOC_IMPORT_ALLOWED_EXTENSIONS", ("pdf", "doc", "docx"))
    )
    max_mb = int(max_size_mb or getattr(settings, "TELEGRAM_DOC_IMPORT_MAX_SIZE_MB", 20))
    max_size = max_mb * 1024 * 1024

    file_ext = _guess_extension(original_name)
    if file_ext not in allowed_ext:
        raise DocumentIngestValidationError(f"unsupported_extension:{file_ext or 'unknown'}")

    if int(size or 0) <= 0:
        raise DocumentIngestValidationError("empty_file")
    if size > max_size:
        raise DocumentIngestValidationError(f"file_too_large:{size}")

    content_hash = _build_content_hash(file_obj)
    meta = source_meta or {}
    existing = None
    if deduplicate:
        existing = (
            Document.objects.filter(size=size, content_hash=content_hash)
            .only("id")
            .order_by("id")
            .first()
        )

    if existing:
        should_enqueue = bool(enqueue_analysis and enqueue_existing)
        if should_enqueue:
            enqueue_documents_for_ai_analysis.delay(document_ids=[existing.id])
        logger.info(
            "document ingest reused document_id=%s source=%s source_meta=%s enqueued=%s",
            existing.id,
            source,
            meta,
            should_enqueue,
        )
        return IngestResult(
            document_id=existing.id,
            created=False,
            reused=True,
            analysis_enqueued=should_enqueue,
            skip_reason="deduplicated",
        )

    document = Document.objects.create(
        file=file_obj,
        original_name=original_name or "",
        mime_type=mime_type or "",
        size=size,
        content_hash=content_hash,
        uploaded_by=uploaded_by,
        description="",
    )
    should_enqueue = bool(enqueue_analysis)
    if should_enqueue:
        enqueue_documents_for_ai_analysis.delay(document_ids=[document.id])

    logger.info(
        "document ingest created document_id=%s source=%s source_meta=%s enqueued=%s",
        document.id,
        source,
        meta,
        should_enqueue,
    )
    return IngestResult(
        document_id=document.id,
        created=True,
        reused=False,
        analysis_enqueued=should_enqueue,
    )
