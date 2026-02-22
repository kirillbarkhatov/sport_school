from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urljoin
from uuid import uuid4

import requests
from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.urls import reverse

logger = logging.getLogger(__name__)

_SIGNED_URL_SALT = "school.document.ai.signed-url"


class AnalyzerContractError(ValueError):
    """Raised when analyzer response does not match expected schema."""


@dataclass
class AnalyzerBatchResult:
    request_id: str
    status: str
    results: list[dict[str, Any]]
    errors: list[dict[str, Any]]


def _as_absolute_url(path_or_url: str) -> str:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        return path_or_url

    base_url = getattr(settings, "SITE_BASE_URL", "").rstrip("/")
    if not base_url:
        raise ValueError("SITE_BASE_URL is required to build absolute document URL")
    return urljoin(f"{base_url}/", path_or_url.lstrip("/"))


def get_document_persistent_url(document) -> str:
    media_url = getattr(settings, "MEDIA_URL", "/media/")
    if not media_url.endswith("/"):
        media_url += "/"
    relative = f"{media_url}{document.file.name}"
    return _as_absolute_url(relative)


def make_signed_document_url(document, *, nonce: str | None = None) -> str:
    storage_backend = getattr(settings, "STORAGE_BACKEND", "filesystem").lower()
    if storage_backend == "s3":
        # For S3-compatible storage (Yandex Object Storage), django-storages
        # generates a fresh presigned URL for each access to file.url.
        return document.file.url

    payload_nonce = nonce or uuid4().hex[:12]
    updated_ts = int(document.updated_at.timestamp()) if document.updated_at else 0
    payload = f"{document.pk}:{updated_ts}:{payload_nonce}"
    token = TimestampSigner(salt=_SIGNED_URL_SALT).sign(payload)
    path = reverse("school:document_signed_access", kwargs={"document_id": document.pk})
    return _as_absolute_url(f"{path}?token={token}")


def validate_signed_document_token(*, document_id: int, token: str, max_age: int) -> tuple[int, int, str]:
    try:
        payload = TimestampSigner(salt=_SIGNED_URL_SALT).unsign(token, max_age=max_age)
    except SignatureExpired as exc:
        raise PermissionError("Signed URL expired") from exc
    except BadSignature as exc:
        raise PermissionError("Signed URL signature is invalid") from exc

    parts = payload.split(":", 2)
    if len(parts) != 3:
        raise PermissionError("Signed URL payload is invalid")

    payload_document_id = int(parts[0])
    if payload_document_id != document_id:
        raise PermissionError("Signed URL document mismatch")

    return payload_document_id, int(parts[1]), parts[2]


def hash_signed_url(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def build_analyzer_payload(
    *,
    request_id: str,
    signed_urls: list[str],
    concurrency: int,
    max_urls: int,
) -> dict[str, Any]:
    if not signed_urls:
        raise ValueError("signed_urls must not be empty")

    payload: dict[str, Any] = {
        "request_id": request_id,
        "urls": signed_urls,
        "options": {
            "concurrency": max(1, int(concurrency)),
            "max_urls": max(1, int(max_urls)),
        },
    }
    return payload


def _validate_date_string(value: Any, *, field_name: str) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AnalyzerContractError(f"{field_name} must be YYYY-MM-DD or null")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise AnalyzerContractError(f"{field_name} must be YYYY-MM-DD") from exc
    return value


def _validate_competition_extracted(extracted: dict[str, Any]) -> dict[str, Any]:
    event_dates = extracted.get("event_dates")
    if not isinstance(event_dates, dict):
        raise AnalyzerContractError("competition_general.extracted.event_dates must be object")

    requirements = extracted.get("requirements")
    if not isinstance(requirements, dict):
        raise AnalyzerContractError("competition_general.extracted.requirements must be object")

    for req_key in ("medical", "insurance", "age_categories", "documents_list"):
        req_values = requirements.get(req_key)
        if not isinstance(req_values, list) or not all(isinstance(item, str) for item in req_values):
            raise AnalyzerContractError(f"competition_general.extracted.requirements.{req_key} must be string[]")

    return {
        "competition_name": extracted.get("competition_name"),
        "discipline": extracted.get("discipline"),
        "season_year": extracted.get("season_year"),
        "region": extracted.get("region"),
        "event_dates": {
            "from": _validate_date_string(event_dates.get("from"), field_name="event_dates.from"),
            "to": _validate_date_string(event_dates.get("to"), field_name="event_dates.to"),
        },
        "location": extracted.get("location"),
        "organizer": extracted.get("organizer"),
        "requirements": requirements,
    }


def _validate_athlete_extracted(extracted: dict[str, Any]) -> dict[str, Any]:
    person = extracted.get("person")
    if not isinstance(person, dict):
        raise AnalyzerContractError("athlete_specific.extracted.person must be object")

    notes = extracted.get("notes")
    if not isinstance(notes, list) or not all(isinstance(item, str) for item in notes):
        raise AnalyzerContractError("athlete_specific.extracted.notes must be string[]")

    admission = extracted.get("admission")
    if admission not in {"allowed", "not_allowed", "unknown"}:
        raise AnalyzerContractError("athlete_specific.extracted.admission must be allowed|not_allowed|unknown")

    # New contract:
    # person = {last_name, first_name, middle_name, birth_date}
    # Keep backward compatibility with old full_name shape.
    last_name = person.get("last_name")
    first_name = person.get("first_name")
    middle_name = person.get("middle_name")
    full_name_legacy = person.get("full_name")
    full_name = full_name_legacy or " ".join(
        part for part in [last_name, first_name, middle_name] if isinstance(part, str) and part.strip()
    ).strip()

    return {
        "person": {
            "last_name": last_name,
            "first_name": first_name,
            "middle_name": middle_name,
            "full_name": full_name or None,
            "birth_date": _validate_date_string(person.get("birth_date"), field_name="person.birth_date"),
        },
        "document_name": extracted.get("document_name"),
        "document_number": extracted.get("document_number"),
        "issue_date": _validate_date_string(extracted.get("issue_date"), field_name="issue_date"),
        "valid_until": _validate_date_string(extracted.get("valid_until"), field_name="valid_until"),
        "organization": extracted.get("organization"),
        "sport": extracted.get("sport"),
        "admission": admission,
        "notes": notes,
    }


def _validate_other_extracted(extracted: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": extracted.get("summary"),
        "guessed_category": extracted.get("guessed_category"),
    }


def _validate_doc_payload(doc_payload: dict[str, Any]) -> dict[str, Any]:
    doc_type = doc_payload.get("doc_type")
    if doc_type not in {"competition_general", "athlete_specific", "other"}:
        raise AnalyzerContractError("doc.doc_type must be competition_general|athlete_specific|other")

    confidence = doc_payload.get("confidence")
    if not isinstance(confidence, (int, float)):
        raise AnalyzerContractError("doc.confidence must be number")
    if confidence < 0 or confidence > 1:
        raise AnalyzerContractError("doc.confidence must be in range 0..1")

    title = doc_payload.get("title")
    if title is not None and not isinstance(title, str):
        raise AnalyzerContractError("doc.title must be string|null")

    extracted = doc_payload.get("extracted")
    if not isinstance(extracted, dict):
        raise AnalyzerContractError("doc.extracted must be object")

    issues = doc_payload.get("issues")
    if not isinstance(issues, list) or not all(isinstance(item, str) for item in issues):
        raise AnalyzerContractError("doc.issues must be string[]")

    if doc_type == "competition_general":
        extracted_norm = _validate_competition_extracted(extracted)
    elif doc_type == "athlete_specific":
        extracted_norm = _validate_athlete_extracted(extracted)
    else:
        extracted_norm = _validate_other_extracted(extracted)

    return {
        "doc_type": doc_type,
        "confidence": float(confidence),
        "title": title,
        "extracted": extracted_norm,
        "issues": issues,
    }


def validate_analyzer_response(data: dict[str, Any]) -> AnalyzerBatchResult:
    request_id = data.get("request_id")
    if not isinstance(request_id, str) or not request_id:
        raise AnalyzerContractError("request_id must be non-empty string")

    status = data.get("status")
    if status not in {"ok", "partial", "error"}:
        raise AnalyzerContractError("status must be ok|partial|error")

    results = data.get("results")
    errors = data.get("errors")
    if not isinstance(results, list):
        raise AnalyzerContractError("results must be array")
    if not isinstance(errors, list):
        raise AnalyzerContractError("errors must be array")

    normalized_results: list[dict[str, Any]] = []
    normalized_errors: list[dict[str, Any]] = []

    for item in results:
        if not isinstance(item, dict):
            raise AnalyzerContractError("results[] item must be object")

        source_url = item.get("source_url")
        ok_value = item.get("ok")
        if not isinstance(source_url, str) or not source_url:
            raise AnalyzerContractError("results[].source_url must be non-empty string")
        if ok_value is not True:
            raise AnalyzerContractError("results[].ok must be true")

        doc_payload = item.get("doc")
        if not isinstance(doc_payload, dict):
            raise AnalyzerContractError("results[].doc must be object")

        normalized_results.append(
            {
                "source_url": source_url,
                "ok": True,
                "doc": _validate_doc_payload(doc_payload),
            }
        )

    for item in errors:
        if not isinstance(item, dict):
            raise AnalyzerContractError("errors[] item must be object")

        source_url = item.get("source_url")
        stage = item.get("stage")
        message = item.get("message")
        if not isinstance(source_url, str) or not source_url:
            raise AnalyzerContractError("errors[].source_url must be non-empty string")
        if stage not in {"download", "openai", "validation"}:
            raise AnalyzerContractError("errors[].stage must be download|openai|validation")
        if not isinstance(message, str) or not message:
            raise AnalyzerContractError("errors[].message must be non-empty string")

        normalized_errors.append(
            {
                "source_url": source_url,
                "stage": stage,
                "message": message,
            }
        )

    return AnalyzerBatchResult(
        request_id=request_id,
        status=status,
        results=normalized_results,
        errors=normalized_errors,
    )


def call_docs_analyzer(*, payload: dict[str, Any]) -> AnalyzerBatchResult:
    base_url = getattr(settings, "DOCS_ANALYZER_URL", "http://192.168.0.4:8001").rstrip("/")
    timeout = float(getattr(settings, "DOCS_ANALYZER_TIMEOUT_SEC", 30))
    url = f"{base_url}/v1/analyze"

    response = requests.post(
        url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, dict):
        raise AnalyzerContractError("Analyzer response must be JSON object")

    result = validate_analyzer_response(data)
    logger.info(
        "docs-analyzer batch completed request_id=%s status=%s results=%s errors=%s",
        result.request_id,
        result.status,
        len(result.results),
        len(result.errors),
    )
    return result
