from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple

import redis
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class XferPayload:
    user_id: int
    next_url: str
    ip: Optional[str]
    ua: Optional[str]
    created_at: float

    @classmethod
    def from_json(cls, raw: str) -> "XferPayload":
        data = json.loads(raw)
        return cls(
            user_id=int(data["user_id"]),
            next_url=data["next_url"],
            ip=data.get("ip"),
            ua=data.get("ua"),
            created_at=float(data["created_at"]),
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "user_id": self.user_id,
                "next_url": self.next_url,
                "ip": self.ip,
                "ua": self.ua,
                "created_at": self.created_at,
            }
        )


def _redis_client() -> redis.Redis:
    url = getattr(settings, "REDIS_XFER_URL", None) or getattr(settings, "REDIS_URL", None) or getattr(settings, "CELERY_BROKER_URL")
    return redis.Redis.from_url(url, decode_responses=True)


def issue_xfer_token(user_id: int, next_url: str, *, ip: Optional[str], ua: Optional[str]) -> str:
    token = uuid.uuid4().hex
    payload = XferPayload(
        user_id=user_id,
        next_url=next_url,
        ip=ip,
        ua=ua,
        created_at=timezone.now().timestamp(),
    )
    ttl = int(getattr(settings, "XFER_TOKEN_TTL_SECONDS", 600))
    client = _redis_client()
    key = f"xfer:{token}"
    client.setex(key, ttl, payload.to_json())
    logger.info("XFER token issued for user=%s next=%s", user_id, next_url)
    return token


def consume_xfer_token(token: str) -> Tuple[Optional[XferPayload], Optional[str]]:
    client = _redis_client()
    key = f"xfer:{token}"
    raw = client.get(key)
    if not raw:
        return None, "expired"
    client.delete(key)
    try:
        payload = XferPayload.from_json(raw)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to decode xfer token payload")
        return None, "invalid"
    return payload, None
