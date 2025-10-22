from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urljoin

import requests
from django.conf import settings

from .models import AssistantSyncLog, ServiceAccount
from .services import get_service_account_for_outgoing, is_integration_enabled

logger = logging.getLogger(__name__)


class AssistantIntegrationError(RuntimeError):
    """Общее исключение для интеграции с AI-помощником."""


class AssistantIntegrationDisabled(AssistantIntegrationError):
    """Интеграция отключена настройками."""


class AssistantServiceAccountMissing(AssistantIntegrationError):
    """Не найден активный сервисный аккаунт для исходящих запросов."""


@dataclass
class AssistantEndpointConfig:
    members: str
    taxonomy: str
    trainings: str

    @classmethod
    def from_settings(cls) -> "AssistantEndpointConfig":
        return cls(
            members=getattr(settings, "AI_ASSISTANT_ENDPOINT_MEMBERS", "/api/v1/sync/members/"),
            taxonomy=getattr(settings, "AI_ASSISTANT_ENDPOINT_TAXONOMY", "/api/v1/sync/taxonomy/"),
            trainings=getattr(settings, "AI_ASSISTANT_ENDPOINT_TRAININGS", "/api/v1/sync/trainings/"),
        )


class AssistantClient:
    """HTTP клиент для обмена данными с AI-помощником."""

    def __init__(self, *, service_account: Optional[ServiceAccount] = None):
        if not is_integration_enabled():
            raise AssistantIntegrationDisabled("Интеграция с AI ассистентом отключена настройками.")

        self.base_url = getattr(settings, "AI_ASSISTANT_BASE_URL", "").rstrip("/")
        if not self.base_url:
            raise AssistantIntegrationError("Не задан AI_ASSISTANT_BASE_URL.")

        self.timeout = float(getattr(settings, "AI_ASSISTANT_TIMEOUT", 10.0))
        self.service_account = service_account or get_service_account_for_outgoing()
        if not self.service_account:
            raise AssistantServiceAccountMissing("Активный сервисный аккаунт не найден.")

        self.endpoints = AssistantEndpointConfig.from_settings()

    def _make_headers(self) -> dict[str, str]:
        token = self.service_account.issue_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _post(self, endpoint: str, payload: dict[str, Any], event_type: str) -> dict[str, Any] | None:
        url = urljoin(f"{self.base_url}/", endpoint.lstrip("/"))
        headers = self._make_headers()

        log = AssistantSyncLog.objects.create(
            service_account=self.service_account,
            direction=AssistantSyncLog.Direction.OUTGOING,
            event_type=event_type,
            request_url=url,
            request_payload=payload,
        )

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
            log.status_code = response.status_code
            content_type = response.headers.get("Content-Type", "")
            if "application/json" in content_type.lower():
                try:
                    log.response_payload = response.json()
                except ValueError:
                    logger.warning("Не удалось распарсить JSON ответ от %s", url)
            else:
                if response.text:
                    log.response_payload = {"raw": response.text[:1000]}
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.exception("Ошибка при работе с AI ассистентом (%s): %s", event_type, exc)
            log.error_message = str(exc)
            log.save(update_fields=["status_code", "response_payload", "error_message"])
            raise AssistantIntegrationError(f"Ошибка при отправке данных: {exc}") from exc
        else:
            log.save(update_fields=["status_code", "response_payload"])

        return log.response_payload

    def send_members_snapshot(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._post(self.endpoints.members, payload, event_type="members.full")

    def send_taxonomy_snapshot(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._post(self.endpoints.taxonomy, payload, event_type="taxonomy.full")

    def send_trainings_snapshot(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        return self._post(self.endpoints.trainings, payload, event_type="trainings.upcoming")
