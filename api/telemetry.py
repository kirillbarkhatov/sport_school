from __future__ import annotations

from datetime import datetime
import logging

logger = logging.getLogger("online_results.telemetry")


def log_event(event: str, **fields: object) -> None:
    payload: list[str] = [f"event={event}"]
    payload.append(f"ts={datetime.now().isoformat()}")
    for key in sorted(fields.keys()):
        value = fields[key]
        text = str(value)
        text = text.replace("\n", "\\n").replace("\r", "")
        if len(text) > 300:
            text = text[:300] + "..."
        payload.append(f"{key}={text}")
    logger.info(" | ".join(payload))
