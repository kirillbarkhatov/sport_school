from __future__ import annotations

from collections import defaultdict
import mimetypes
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

import requests
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from celery.utils.log import get_task_logger
from django.conf import settings
from django.core.files.storage import default_storage
from django.db.models import Count
from django.utils import timezone

from .document_ai import (
    AnalyzerContractError,
    build_analyzer_payload,
    call_docs_analyzer,
    get_document_persistent_url,
    hash_signed_url,
    make_signed_document_url,
)
from .document_binding import auto_bind_document_by_analysis
from .models import AthleteContract, Document, DocumentAIAnalysis
from .services import ensure_monthly_service_for_contract

logger = get_task_logger(__name__)


@shared_task
def ensure_monthly_contract_services():
    today = timezone.now().date()
    contracts = AthleteContract.objects.select_related("profile__family", "profile__athlete__person")
    for contract in contracts:
        ensure_monthly_service_for_contract(contract, today)


def _queue_name() -> str:
    return getattr(settings, "CELERY_QUEUE_DOCS_ANALYSIS", "docs_analysis")


def _batch_size() -> int:
    return max(1, int(getattr(settings, "DOCS_ANALYZER_BATCH_SIZE", 20)))


def _retry_kwargs() -> dict[str, Any]:
    return {"max_retries": int(getattr(settings, "DOCS_ANALYZER_RETRY_MAX", 4))}


def _needs_analysis(document: Document, *, force: bool = False) -> bool:
    if force:
        return True

    analysis = getattr(document, "ai_analysis", None)
    if analysis is None:
        return True

    if not analysis.is_analyzed_successfully:
        return True

    snapshot = analysis.document_updated_at_snapshot
    if snapshot is None:
        return True

    return snapshot < document.updated_at


def _document_context_key(document: Document) -> str:
    has_competition = bool(getattr(document, "competition_links_count", 0))
    has_athlete = bool(getattr(document, "athlete_links_count", 0))
    if has_competition and not has_athlete:
        return "competition"
    if has_athlete and not has_competition:
        return "athlete"
    if has_competition and has_athlete:
        return "mixed"
    return "other"


def _build_batch_request_id(context_key: str, idx: int) -> str:
    ts = timezone.now().strftime("%Y%m%d-%H%M%S")
    return f"django-batch-{context_key}-{ts}-{idx:03d}-{uuid4().hex[:8]}"


def _iter_storage_files(prefix: str):
    stack = [prefix.strip("/")]
    while stack:
        current = stack.pop()
        try:
            dirs, files = default_storage.listdir(current)
        except Exception:
            continue

        for subdir in dirs:
            next_path = f"{current}/{subdir}" if current else subdir
            stack.append(next_path)

        for filename in files:
            yield f"{current}/{filename}" if current else filename


def _upsert_document_from_storage(path: str) -> tuple[int | None, bool]:
    normalized_path = path.strip("/")
    if not normalized_path:
        return None, False

    original_name = PurePosixPath(normalized_path).name
    guessed_mime = mimetypes.guess_type(original_name)[0] or ""
    try:
        size = int(default_storage.size(normalized_path))
    except Exception:
        size = 0

    document, created = Document.objects.get_or_create(
        file=normalized_path,
        defaults={
            "original_name": original_name,
            "mime_type": guessed_mime,
            "size": size,
            "description": "",
        },
    )

    changed = created
    if not created:
        update_fields = []
        if document.original_name != original_name:
            document.original_name = original_name
            update_fields.append("original_name")
        if document.mime_type != guessed_mime:
            document.mime_type = guessed_mime
            update_fields.append("mime_type")
        if document.size != size:
            document.size = size
            update_fields.append("size")

        if update_fields:
            update_fields.append("updated_at")
            document.save(update_fields=update_fields)
            changed = True

    return document.id, changed


def _upsert_analysis_error(
    *,
    document: Document,
    request_id: str,
    status: str,
    error_stage: str | None,
    error_message: str,
    source_signed_url_hash: str,
) -> None:
    defaults = {
        "status": status,
        "request_id": request_id,
        "doc_type": None,
        "confidence": None,
        "title": None,
        "extracted": {},
        "issues": [],
        "raw_response": {},
        "error_stage": error_stage,
        "error_message": error_message,
        "source_persistent_url": get_document_persistent_url(document),
        "source_signed_url_hash": source_signed_url_hash,
        "is_analyzed_successfully": False,
        "document_updated_at_snapshot": document.updated_at,
        "analyzed_at": timezone.now(),
    }
    DocumentAIAnalysis.objects.update_or_create(document=document, defaults=defaults)


def _upsert_analysis_pending(*, document: Document, request_id: str = "") -> None:
    defaults = {
        "status": DocumentAIAnalysis.Status.PENDING,
        "request_id": request_id,
        "error_stage": None,
        "error_message": None,
        "source_persistent_url": get_document_persistent_url(document),
        "is_analyzed_successfully": False,
        "document_updated_at_snapshot": document.updated_at,
    }
    DocumentAIAnalysis.objects.update_or_create(document=document, defaults=defaults)


def _apply_result_to_analysis(
    *,
    document: Document,
    request_id: str,
    source_signed_url_hash: str,
    result_item: dict[str, Any] | None,
    error_item: dict[str, Any] | None,
    batch_status: str,
) -> None:
    persistent_url = get_document_persistent_url(document)

    if result_item:
        doc_payload = result_item["doc"]
        defaults = {
            "status": DocumentAIAnalysis.Status.OK,
            "request_id": request_id,
            "doc_type": doc_payload["doc_type"],
            "confidence": doc_payload["confidence"],
            "title": doc_payload.get("title"),
            "extracted": doc_payload["extracted"],
            "issues": doc_payload["issues"],
            "raw_response": {
                "batch_status": batch_status,
                "result": result_item,
            },
            "error_stage": None,
            "error_message": None,
            "source_persistent_url": persistent_url,
            "source_signed_url_hash": source_signed_url_hash,
            "auto_bound": False,
            "bound_entity_type": None,
            "bound_entity_id": None,
            "bound_at": None,
            "is_analyzed_successfully": True,
            "document_updated_at_snapshot": document.updated_at,
            "analyzed_at": timezone.now(),
        }
        DocumentAIAnalysis.objects.update_or_create(document=document, defaults=defaults)
        return

    if error_item:
        stage = error_item["stage"]
        if stage == "download":
            status = DocumentAIAnalysis.Status.FAILED_DOWNLOAD
        elif stage == "openai":
            status = DocumentAIAnalysis.Status.FAILED_OPENAI
        else:
            status = DocumentAIAnalysis.Status.FAILED_VALIDATION

        defaults = {
            "status": status,
            "request_id": request_id,
            "doc_type": None,
            "confidence": None,
            "title": None,
            "extracted": {},
            "issues": [],
            "raw_response": {
                "batch_status": batch_status,
                "error": error_item,
            },
            "error_stage": stage,
            "error_message": error_item["message"],
            "source_persistent_url": persistent_url,
            "source_signed_url_hash": source_signed_url_hash,
            "auto_bound": False,
            "bound_entity_type": None,
            "bound_entity_id": None,
            "bound_at": None,
            "is_analyzed_successfully": False,
            "document_updated_at_snapshot": document.updated_at,
            "analyzed_at": timezone.now(),
        }
        DocumentAIAnalysis.objects.update_or_create(document=document, defaults=defaults)
        return

    defaults = {
        "status": DocumentAIAnalysis.Status.ERROR,
        "request_id": request_id,
        "doc_type": None,
        "confidence": None,
        "title": None,
        "extracted": {},
        "issues": [],
        "raw_response": {
            "batch_status": batch_status,
        },
        "error_stage": None,
        "error_message": "Документ отсутствует в results/errors ответа анализатора",
        "source_persistent_url": persistent_url,
        "source_signed_url_hash": source_signed_url_hash,
        "auto_bound": False,
        "bound_entity_type": None,
        "bound_entity_id": None,
        "bound_at": None,
        "is_analyzed_successfully": False,
        "document_updated_at_snapshot": document.updated_at,
        "analyzed_at": timezone.now(),
    }
    DocumentAIAnalysis.objects.update_or_create(document=document, defaults=defaults)


@shared_task
def enqueue_documents_for_ai_analysis(document_ids: list[int] | None = None, *, force: bool = False) -> dict[str, int]:
    queryset = (
        Document.objects.all()
        .select_related("ai_analysis")
        .annotate(
            competition_links_count=Count("competition_links", distinct=True),
            athlete_links_count=Count("athlete_links", distinct=True),
        )
        .order_by("id")
    )

    if document_ids:
        queryset = queryset.filter(id__in=document_ids)

    documents = [doc for doc in queryset if _needs_analysis(doc, force=force)]
    if not documents:
        logger.info("docs-ai enqueue skipped: no documents to analyze")
        return {"selected": 0, "queued": 0}

    for doc in documents:
        _upsert_analysis_pending(document=doc)

    grouped: dict[str, list[int]] = defaultdict(list)
    for doc in documents:
        grouped[_document_context_key(doc)].append(doc.id)

    total_queued = 0
    batch_size = _batch_size()
    for context_key, ids in grouped.items():
        for idx, start in enumerate(range(0, len(ids), batch_size), start=1):
            batch_ids = ids[start : start + batch_size]
            request_id = _build_batch_request_id(context_key, idx)
            analyze_documents_batch_task.apply_async(
                kwargs={"document_ids": batch_ids, "request_id": request_id, "force": force},
                queue=_queue_name(),
            )
            total_queued += len(batch_ids)

    logger.info(
        "docs-ai enqueue completed selected=%s queued=%s groups=%s",
        len(documents),
        total_queued,
        len(grouped),
    )
    return {"selected": len(documents), "queued": total_queued}


@shared_task
def sync_documents_from_storage_task(*, prefix: str | None = None, enqueue_analysis: bool = True) -> dict[str, int]:
    docs_prefix = (prefix or getattr(settings, "DOCS_STORAGE_SYNC_PREFIX", "documents")).strip("/")
    created_or_changed_document_ids: list[int] = []
    scanned_document_ids: list[int] = []
    scanned = 0

    for path in _iter_storage_files(docs_prefix):
        scanned += 1
        document_id, changed = _upsert_document_from_storage(path)
        if not document_id:
            continue
        scanned_document_ids.append(document_id)
        if changed:
            created_or_changed_document_ids.append(document_id)

    queued = 0
    selected = 0
    if enqueue_analysis and scanned_document_ids:
        enqueue_result = enqueue_documents_for_ai_analysis(document_ids=scanned_document_ids)
        selected = int(enqueue_result.get("selected", 0))
        queued = int(enqueue_result.get("queued", 0))

    logger.info(
        "docs-sync completed prefix=%s scanned=%s changed=%s selected_for_analysis=%s queued=%s",
        docs_prefix,
        scanned,
        len(created_or_changed_document_ids),
        selected,
        queued,
    )
    return {
        "scanned": scanned,
        "changed": len(created_or_changed_document_ids),
        "selected_for_analysis": selected,
        "queued": queued,
    }


@shared_task(bind=True)
def analyze_documents_batch_task(self, *, document_ids: list[int], request_id: str, force: bool = False) -> dict[str, Any]:
    documents = [
        document
        for document in Document.objects.filter(id__in=document_ids).select_related("ai_analysis").order_by("id")
        if _needs_analysis(document, force=force)
    ]
    if not documents:
        logger.info("docs-ai batch skipped request_id=%s reason=no_documents_to_analyze", request_id)
        return {"request_id": request_id, "processed": 0}

    for document in documents:
        _upsert_analysis_pending(document=document, request_id=request_id)

    signed_url_to_document: dict[str, Document] = {}
    for document in documents:
        signed_url = make_signed_document_url(document)
        signed_url_to_document[signed_url] = document

    payload = build_analyzer_payload(
        request_id=request_id,
        signed_urls=list(signed_url_to_document.keys()),
        concurrency=int(getattr(settings, "DOCS_ANALYZER_CONCURRENCY", 5)),
        max_urls=int(getattr(settings, "DOCS_ANALYZER_MAX_URLS", _batch_size())),
    )

    logger.info(
        "docs-ai batch start request_id=%s documents=%s task_id=%s",
        request_id,
        len(documents),
        getattr(self.request, "id", None),
    )

    try:
        batch_result = call_docs_analyzer(payload=payload)
    except requests.RequestException as exc:
        max_retries = int(getattr(settings, "DOCS_ANALYZER_RETRY_MAX", 4))
        base_backoff = int(getattr(settings, "DOCS_ANALYZER_RETRY_BACKOFF_SEC", 15))
        countdown = base_backoff * (2 ** max(self.request.retries, 0))
        retries_left = max_retries - self.request.retries
        logger.warning(
            "docs-ai batch network/openai error request_id=%s retry=%s retries_left=%s countdown=%s error=%s",
            request_id,
            self.request.retries,
            retries_left,
            countdown,
            exc,
        )
        try:
            raise self.retry(exc=exc, countdown=countdown, **_retry_kwargs())
        except MaxRetriesExceededError:
            for signed_url, document in signed_url_to_document.items():
                _upsert_analysis_error(
                    document=document,
                    request_id=request_id,
                    status=DocumentAIAnalysis.Status.FAILED_OPENAI,
                    error_stage=DocumentAIAnalysis.ErrorStage.OPENAI,
                    error_message=str(exc),
                    source_signed_url_hash=hash_signed_url(signed_url),
                )
            raise
    except AnalyzerContractError as exc:
        logger.error("docs-ai batch contract error request_id=%s error=%s", request_id, exc)
        for signed_url, document in signed_url_to_document.items():
            _upsert_analysis_error(
                document=document,
                request_id=request_id,
                status=DocumentAIAnalysis.Status.FAILED_VALIDATION,
                error_stage=DocumentAIAnalysis.ErrorStage.VALIDATION,
                error_message=str(exc),
                source_signed_url_hash=hash_signed_url(signed_url),
            )
        return {"request_id": request_id, "processed": len(documents), "status": "failed_validation"}

    results_map = {item["source_url"]: item for item in batch_result.results}
    errors_map = {item["source_url"]: item for item in batch_result.errors}

    for signed_url, document in signed_url_to_document.items():
        result_item = results_map.get(signed_url)
        error_item = errors_map.get(signed_url)
        _apply_result_to_analysis(
            document=document,
            request_id=batch_result.request_id,
            source_signed_url_hash=hash_signed_url(signed_url),
            result_item=result_item,
            error_item=error_item,
            batch_status=batch_result.status,
        )
        if result_item:
            bind_result = auto_bind_document_by_analysis(document)
            if bind_result.bound and hasattr(document, "ai_analysis"):
                DocumentAIAnalysis.objects.filter(document=document).update(
                    auto_bound=True,
                    bound_entity_type=bind_result.entity_type,
                    bound_entity_id=bind_result.entity_id,
                    bound_at=timezone.now(),
                )

    logger.info(
        "docs-ai batch complete request_id=%s status=%s documents=%s results=%s errors=%s",
        batch_result.request_id,
        batch_result.status,
        len(documents),
        len(batch_result.results),
        len(batch_result.errors),
    )
    return {
        "request_id": batch_result.request_id,
        "status": batch_result.status,
        "processed": len(documents),
        "results": len(batch_result.results),
        "errors": len(batch_result.errors),
    }
