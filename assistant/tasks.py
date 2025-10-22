from __future__ import annotations

from typing import Any

from celery import shared_task
from celery.utils.log import get_task_logger

from .client import (
    AssistantClient,
    AssistantIntegrationDisabled,
    AssistantIntegrationError,
    AssistantServiceAccountMissing,
)
from .services import (
    build_members_payload,
    build_taxonomy_payload,
    build_upcoming_trainings_payload,
    is_integration_enabled,
)

logger = get_task_logger(__name__)


def _prepare_client() -> AssistantClient | None:
    if not is_integration_enabled():
        logger.info("AI assistant integration disabled. Skipping task.")
        return None
    try:
        return AssistantClient()
    except AssistantIntegrationDisabled:
        logger.info("AI assistant integration disabled by configuration.")
    except AssistantServiceAccountMissing as exc:
        logger.error("Service account missing for assistant integration: %s", exc)
    except AssistantIntegrationError as exc:
        logger.error("Assistant integration misconfigured: %s", exc)
    return None


@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def send_members_snapshot_task(self) -> dict[str, Any] | str:
    client = _prepare_client()
    if not client:
        return "skipped"

    payload = build_members_payload()
    try:
        client.send_members_snapshot(payload)
    except AssistantIntegrationError as exc:
        logger.warning("Failed to send members snapshot: %s", exc)
        raise self.retry(exc=exc)
    logger.info("Sent %d members to AI assistant.", len(payload.get("members", [])))
    return {"members": len(payload.get("members", []))}


@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def send_taxonomy_snapshot_task(self) -> dict[str, Any] | str:
    client = _prepare_client()
    if not client:
        return "skipped"

    payload = build_taxonomy_payload()
    try:
        client.send_taxonomy_snapshot(payload)
    except AssistantIntegrationError as exc:
        logger.warning("Failed to send taxonomy snapshot: %s", exc)
        raise self.retry(exc=exc)
    logger.info("Sent taxonomy snapshot to AI assistant.")
    return {"counts": {k: len(v) for k, v in payload.items()}}


@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def send_upcoming_trainings_snapshot_task(self) -> dict[str, Any] | str:
    client = _prepare_client()
    if not client:
        return "skipped"

    payload = build_upcoming_trainings_payload()
    try:
        client.send_trainings_snapshot(payload)
    except AssistantIntegrationError as exc:
        logger.warning("Failed to send trainings snapshot: %s", exc)
        raise self.retry(exc=exc)
    logger.info(
        "Sent %d trainings to AI assistant.",
        len(payload.get("trainings", [])),
    )
    return {"trainings": len(payload.get("trainings", []))}


def trigger_members_sync(*, countdown: int = 0) -> None:
    if not is_integration_enabled():
        return
    send_members_snapshot_task.apply_async(countdown=countdown)


def trigger_taxonomy_sync(*, countdown: int = 0) -> None:
    if not is_integration_enabled():
        return
    send_taxonomy_snapshot_task.apply_async(countdown=countdown)


def trigger_trainings_sync(*, countdown: int = 0) -> None:
    if not is_integration_enabled():
        return
    send_upcoming_trainings_snapshot_task.apply_async(countdown=countdown)
