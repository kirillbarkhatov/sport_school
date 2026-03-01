from __future__ import annotations

from collections import defaultdict
from datetime import date
import mimetypes
import random
import time
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID
from uuid import uuid4

import requests
from celery import chain, shared_task
from celery.exceptions import MaxRetriesExceededError
from celery.utils.log import get_task_logger
from django.conf import settings
from django.core.cache import cache
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
from .document_binding import infer_athlete_doc_type
from .document_binding import sync_athlete_document_from_analysis
from .models import AthleteContract, AthleteDocument, Document, DocumentAIAnalysis, DocumentAIJob, DocumentAIJobItem, DocumentType
from .services import ensure_monthly_service_for_contract

logger = get_task_logger(__name__)


def _monitor_cache_key(athlete_document_id: int) -> str:
    return f"athlete-cert-monitor:{athlete_document_id}"


@shared_task
def ensure_monthly_contract_services():
    today = timezone.now().date()
    contracts = AthleteContract.objects.select_related("profile__family", "profile__athlete__person")
    for contract in contracts:
        ensure_monthly_service_for_contract(contract, today)


@shared_task
def reconcile_athlete_certificate_validity() -> dict[str, int]:
    today = timezone.localdate()
    updated = 0
    need_clarification = 0
    marked_inactive = 0

    med_docs = (
        AthleteDocument.objects.select_related("document__ai_analysis")
        .filter(doc_type=DocumentType.MED_CERT)
        .order_by("athlete_id", "-created_at")
    )

    by_athlete: dict[int, list[AthleteDocument]] = defaultdict(list)
    for link in med_docs:
        analysis = getattr(link.document, "ai_analysis", None)
        extracted = (analysis.extracted or {}) if analysis else {}
        ai_valid_until = extracted.get("valid_until")
        changed_fields: list[str] = []
        if not link.valid_until and ai_valid_until:
            try:
                parsed_date = date.fromisoformat(str(ai_valid_until))
                link.valid_until = parsed_date
                changed_fields.append("valid_until")
            except Exception:
                pass

        clarification = link.valid_until is None
        if link.needs_valid_until_clarification != clarification:
            link.needs_valid_until_clarification = clarification
            changed_fields.append("needs_valid_until_clarification")
            need_clarification += int(clarification)
            updated += 1

        if link.valid_until and link.valid_until < today and link.is_actual:
            link.is_actual = False
            changed_fields.append("is_actual")
            marked_inactive += 1
            updated += 1

        if link.valid_until and clarification:
            link.needs_valid_until_clarification = False
            changed_fields.append("needs_valid_until_clarification")
            updated += 1

        if changed_fields:
            link.save(update_fields=list(set(changed_fields)))
        by_athlete[link.athlete_id].append(link)

    for athlete_id, docs in by_athlete.items():
        valid_docs = [doc for doc in docs if doc.valid_until and doc.valid_until >= today]
        if valid_docs:
            active_id = valid_docs[0].id
        else:
            pending_docs = [doc for doc in docs if doc.needs_valid_until_clarification]
            active_id = pending_docs[0].id if pending_docs else None
        for doc in docs:
            should_be_active = active_id is not None and doc.id == active_id
            if doc.is_actual != should_be_active:
                doc.is_actual = should_be_active
                doc.save(update_fields=["is_actual"])
                updated += 1

    return {
        "updated": updated,
        "need_clarification": need_clarification,
        "marked_inactive": marked_inactive,
    }


@shared_task
def monitor_athlete_document_analysis_status(*, athlete_document_id: int, timeout_seconds: int = 60) -> dict[str, Any]:
    key = _monitor_cache_key(int(athlete_document_id))
    deadline = time.time() + max(10, int(timeout_seconds))
    final_payload: dict[str, Any] = {
        "state": "processing",
        "message": "Документ отправлен в обработку",
    }

    while time.time() < deadline:
        link = (
            AthleteDocument.objects.select_related("document__ai_analysis")
            .filter(pk=athlete_document_id)
            .first()
        )
        if not link:
            final_payload = {"state": "failed", "message": "Документ не найден"}
            cache.set(key, final_payload, timeout=180)
            return final_payload

        analysis = getattr(link.document, "ai_analysis", None)
        if not analysis or analysis.status == DocumentAIAnalysis.Status.PENDING:
            final_payload = {"state": "processing", "message": "анализируем документ", "ai_status": "pending"}
            cache.set(key, final_payload, timeout=180)
            time.sleep(1)
            continue

        if analysis.is_analyzed_successfully:
            sync_athlete_document_from_analysis(link.document)
            link.refresh_from_db()
            if (
                analysis.doc_type != DocumentAIAnalysis.DocType.ATHLETE_SPECIFIC
                or infer_athlete_doc_type(analysis) != DocumentType.MED_CERT
            ):
                final_payload = {
                    "state": "unrecognized",
                    "message": "Справка не распознана",
                }
            elif link.valid_until:
                final_payload = {
                    "state": "done",
                    "message": "Справка распознана",
                    "valid_until": link.valid_until.isoformat(),
                }
            else:
                final_payload = {
                    "state": "need_clarification",
                    "message": "Требуется уточнение по справке",
                }
            cache.set(key, final_payload, timeout=180)
            return final_payload

        if analysis.status in {
            DocumentAIAnalysis.Status.FAILED_DOWNLOAD,
            DocumentAIAnalysis.Status.FAILED_OPENAI,
            DocumentAIAnalysis.Status.FAILED_VALIDATION,
            DocumentAIAnalysis.Status.ERROR,
        }:
            final_payload = {
                "state": "failed",
                "message": analysis.error_message or "Ошибка обработки документа",
                "ai_status": analysis.status,
            }
            cache.set(key, final_payload, timeout=180)
            return final_payload

        final_payload = {"state": "processing", "message": "AI обрабатывает документ", "ai_status": analysis.status}
        cache.set(key, final_payload, timeout=180)
        time.sleep(1)

    cache.set(key, final_payload, timeout=180)
    return final_payload


@shared_task
def rebind_unbound_competition_documents_task(*, limit: int = 0) -> dict[str, int]:
    queryset = (
        Document.objects.select_related("ai_analysis")
        .filter(
            ai_analysis__is_analyzed_successfully=True,
            ai_analysis__doc_type=DocumentAIAnalysis.DocType.COMPETITION_GENERAL,
            competition_links__isnull=True,
            athlete_links__isnull=True,
        )
        .order_by("-ai_analysis__analyzed_at", "id")
        .distinct()
    )
    if limit and limit > 0:
        queryset = queryset[:limit]

    processed = 0
    bound = 0
    for document in queryset:
        processed += 1
        bind_result = auto_bind_document_by_analysis(document)
        if not bind_result.bound:
            continue
        DocumentAIAnalysis.objects.filter(document=document).update(
            auto_bound=True,
            bound_entity_type=bind_result.entity_type,
            bound_entity_id=bind_result.entity_id,
            bound_at=timezone.now(),
        )
        bound += 1

    logger.info("docs-ai rebind-unbound completed processed=%s bound=%s", processed, bound)
    return {
        "processed": processed,
        "bound": bound,
    }


def _queue_name() -> str:
    return getattr(settings, "CELERY_QUEUE_DOCS_ANALYSIS", "docs_analysis")


def _batch_size() -> int:
    return max(1, int(getattr(settings, "DOCS_ANALYZER_BATCH_SIZE", 3)))


def _retry_kwargs() -> dict[str, Any]:
    return {"max_retries": int(getattr(settings, "DOCS_ANALYZER_RETRY_MAX", 1))}


def _to_job_uuid(job_id: str | UUID | None) -> UUID | None:
    if job_id is None:
        return None
    if isinstance(job_id, UUID):
        return job_id
    try:
        return UUID(str(job_id))
    except (TypeError, ValueError):
        logger.warning("docs-ai invalid job_id=%s", job_id)
        return None


def _job_query(job_id: str | UUID | None):
    parsed_job_id = _to_job_uuid(job_id)
    if parsed_job_id is None:
        return DocumentAIJob.objects.none()
    return DocumentAIJob.objects.filter(pk=parsed_job_id)


def _job_item_query(job_id: str | UUID | None):
    parsed_job_id = _to_job_uuid(job_id)
    if parsed_job_id is None:
        return DocumentAIJobItem.objects.none()
    return DocumentAIJobItem.objects.filter(job_id=parsed_job_id)


def _reserve_budget_counter(*, key: str, amount: int, limit: int) -> bool:
    if limit <= 0 or amount <= 0:
        return True

    cache.add(key, 0, timeout=90)
    try:
        current = int(cache.incr(key, amount))
    except Exception:
        current = int(cache.get(key, 0) or 0) + amount
        cache.set(key, current, timeout=90)

    if current <= limit:
        return True

    try:
        cache.decr(key, amount)
    except Exception:
        fallback_value = max(0, int(cache.get(key, 0) or 0) - amount)
        cache.set(key, fallback_value, timeout=90)
    return False


def _try_reserve_docs_analyzer_budget(*, document_count: int) -> bool:
    minute_key = timezone.now().strftime("%Y%m%d%H%M")
    docs_per_min_limit = int(getattr(settings, "DOCS_ANALYZER_DOCS_PER_MIN_LIMIT", 0))
    tokens_per_min_limit = int(getattr(settings, "DOCS_ANALYZER_TOKENS_PER_MIN_LIMIT", 0))
    tokens_per_doc = int(getattr(settings, "DOCS_ANALYZER_TOKENS_PER_DOC_ESTIMATE", 0))

    docs_ok = _reserve_budget_counter(
        key=f"docs-ai:budget:docs:{minute_key}",
        amount=max(0, int(document_count)),
        limit=docs_per_min_limit,
    )
    if not docs_ok:
        return False

    estimated_tokens = max(0, int(document_count)) * max(0, tokens_per_doc)
    if estimated_tokens <= 0 or tokens_per_min_limit <= 0:
        return True

    tokens_ok = _reserve_budget_counter(
        key=f"docs-ai:budget:tokens:{minute_key}",
        amount=estimated_tokens,
        limit=tokens_per_min_limit,
    )
    return tokens_ok


def _refresh_job_status(job_id: str | UUID | None) -> None:
    job_qs = _job_query(job_id)
    job = job_qs.first()
    if job is None:
        return

    items_qs = DocumentAIJobItem.objects.filter(job=job)
    total = int(job.total_documents)
    pending = items_qs.filter(status=DocumentAIJobItem.Status.PENDING).count()
    in_progress = items_qs.filter(status=DocumentAIJobItem.Status.IN_PROGRESS).count()
    completed = items_qs.filter(status=DocumentAIJobItem.Status.COMPLETED).count()
    failed = items_qs.filter(status=DocumentAIJobItem.Status.FAILED).count()

    now = timezone.now()
    update_fields: list[str] = []
    if total == 0:
        next_status = DocumentAIJob.Status.COMPLETED
    elif pending > 0 or in_progress > 0:
        next_status = DocumentAIJob.Status.IN_PROGRESS
    elif failed > 0:
        next_status = DocumentAIJob.Status.FAILED
    else:
        next_status = DocumentAIJob.Status.COMPLETED

    if job.status != next_status:
        job.status = next_status
        update_fields.append("status")

    if next_status == DocumentAIJob.Status.IN_PROGRESS and job.started_at is None:
        job.started_at = now
        update_fields.append("started_at")

    if next_status in {DocumentAIJob.Status.COMPLETED, DocumentAIJob.Status.FAILED} and job.finished_at is None:
        job.finished_at = now
        update_fields.append("finished_at")

    if update_fields:
        update_fields.append("updated_at")
        job.save(update_fields=update_fields)


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


def create_documents_ai_job(
    *,
    document_ids: list[int] | None = None,
    force: bool = False,
    created_by_id: int | None = None,
) -> DocumentAIJob:
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
    job = DocumentAIJob.objects.create(
        created_by_id=created_by_id,
        force=force,
        status=DocumentAIJob.Status.PENDING,
        total_documents=len(documents),
    )

    if documents:
        DocumentAIJobItem.objects.bulk_create(
            [
                DocumentAIJobItem(
                    job=job,
                    document=doc,
                    status=DocumentAIJobItem.Status.PENDING,
                )
                for doc in documents
            ]
        )
        enqueue_documents_for_ai_analysis.delay(
            document_ids=[doc.id for doc in documents],
            force=force,
            job_id=str(job.id),
        )
    else:
        job.status = DocumentAIJob.Status.COMPLETED
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at", "updated_at"])

    return job


@shared_task
def enqueue_documents_for_ai_analysis(
    document_ids: list[int] | None = None,
    *,
    force: bool = False,
    job_id: str | None = None,
) -> dict[str, int]:
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
        _refresh_job_status(job_id)
        return {"selected": 0, "queued": 0}

    for doc in documents:
        _upsert_analysis_pending(document=doc)

    grouped: dict[str, list[int]] = defaultdict(list)
    for doc in documents:
        grouped[_document_context_key(doc)].append(doc.id)

    total_queued = 0
    batch_size = _batch_size()
    signatures = []
    for context_key in sorted(grouped.keys()):
        ids = grouped[context_key]
        for idx, start in enumerate(range(0, len(ids), batch_size), start=1):
            batch_ids = ids[start : start + batch_size]
            request_id = _build_batch_request_id(context_key, idx)
            signatures.append(
                analyze_documents_batch_task.si(
                    document_ids=batch_ids,
                    request_id=request_id,
                    force=force,
                    job_id=job_id,
                ).set(queue=_queue_name())
            )
            total_queued += len(batch_ids)

    if signatures:
        chain(*signatures).apply_async(queue=_queue_name())

    logger.info(
        "docs-ai enqueue completed selected=%s queued=%s groups=%s",
        len(documents),
        total_queued,
        len(grouped),
    )
    if job_id:
        _refresh_job_status(job_id)
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


@shared_task(bind=True, acks_late=True)
def analyze_documents_batch_task(
    self,
    *,
    document_ids: list[int],
    request_id: str,
    force: bool = False,
    job_id: str | None = None,
) -> dict[str, Any]:
    documents = [
        document
        for document in Document.objects.filter(id__in=document_ids).select_related("ai_analysis").order_by("id")
        if _needs_analysis(document, force=force)
    ]
    if not documents:
        logger.info("docs-ai batch skipped request_id=%s reason=no_documents_to_analyze", request_id)
        _refresh_job_status(job_id)
        return {"request_id": request_id, "processed": 0}

    _job_item_query(job_id).filter(document_id__in=[doc.id for doc in documents]).update(
        status=DocumentAIJobItem.Status.IN_PROGRESS,
        request_id=request_id,
        error_message=None,
    )
    _refresh_job_status(job_id)

    while not _try_reserve_docs_analyzer_budget(document_count=len(documents)):
        defer_base = int(getattr(settings, "DOCS_ANALYZER_BUDGET_BACKOFF_SEC", 20))
        defer_jitter = int(getattr(settings, "DOCS_ANALYZER_BUDGET_BACKOFF_JITTER_SEC", 5))
        defer_countdown = defer_base + random.randint(0, max(0, defer_jitter))
        _job_item_query(job_id).filter(document_id__in=[doc.id for doc in documents]).update(
            status=DocumentAIJobItem.Status.IN_PROGRESS,
            error_message=f"Ожидание budget guard ({defer_countdown}s)",
        )
        logger.info(
            "docs-ai batch waiting for budget request_id=%s documents=%s sleep=%s",
            request_id,
            len(documents),
            defer_countdown,
        )
        time.sleep(defer_countdown)

    for document in documents:
        _upsert_analysis_pending(document=document, request_id=request_id)

    signed_url_to_document: dict[str, Document] = {}
    for document in documents:
        signed_url = make_signed_document_url(document)
        signed_url_to_document[signed_url] = document

    payload = build_analyzer_payload(
        request_id=request_id,
        signed_urls=list(signed_url_to_document.keys()),
        concurrency=int(getattr(settings, "DOCS_ANALYZER_CONCURRENCY", 2)),
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
        max_retries = int(getattr(settings, "DOCS_ANALYZER_RETRY_MAX", 1))
        base_backoff = int(getattr(settings, "DOCS_ANALYZER_RETRY_BACKOFF_SEC", 15))
        jitter_sec = int(getattr(settings, "DOCS_ANALYZER_RETRY_JITTER_SEC", 5))
        countdown = base_backoff * (2 ** max(self.request.retries, 0)) + random.randint(0, max(0, jitter_sec))
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
            _job_item_query(job_id).filter(document_id__in=[doc.id for doc in documents]).update(
                status=DocumentAIJobItem.Status.FAILED,
                request_id=request_id,
                error_message=str(exc),
            )
            _refresh_job_status(job_id)
            return {
                "request_id": request_id,
                "status": "failed_openai",
                "processed": len(documents),
                "results": 0,
                "errors": len(documents),
            }
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
        _job_item_query(job_id).filter(document_id__in=[doc.id for doc in documents]).update(
            status=DocumentAIJobItem.Status.FAILED,
            request_id=request_id,
            error_message=str(exc),
        )
        _refresh_job_status(job_id)
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
            sync_athlete_document_from_analysis(document)
            if bind_result.bound and hasattr(document, "ai_analysis"):
                DocumentAIAnalysis.objects.filter(document=document).update(
                    auto_bound=True,
                    bound_entity_type=bind_result.entity_type,
                    bound_entity_id=bind_result.entity_id,
                    bound_at=timezone.now(),
                )
            _job_item_query(job_id).filter(document_id=document.id).update(
                status=DocumentAIJobItem.Status.COMPLETED,
                request_id=batch_result.request_id,
                error_message=None,
            )
        else:
            error_message = "Документ отсутствует в results/errors ответа анализатора"
            if error_item:
                error_message = error_item.get("message", error_message)
            _job_item_query(job_id).filter(document_id=document.id).update(
                status=DocumentAIJobItem.Status.FAILED,
                request_id=batch_result.request_id,
                error_message=error_message,
            )

    logger.info(
        "docs-ai batch complete request_id=%s status=%s documents=%s results=%s errors=%s",
        batch_result.request_id,
        batch_result.status,
        len(documents),
        len(batch_result.results),
        len(batch_result.errors),
    )
    _refresh_job_status(job_id)
    return {
        "request_id": batch_result.request_id,
        "status": batch_result.status,
        "processed": len(documents),
        "results": len(batch_result.results),
        "errors": len(batch_result.errors),
    }
