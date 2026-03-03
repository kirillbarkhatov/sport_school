from __future__ import annotations

import re
from typing import Optional, Tuple

PHONE_CLEAN_PATTERN = re.compile(r"\D+")
TG_URL_PATTERN = re.compile(r"^(?:https?://)?t\.me/(?P<value>[\w@+\d_]+)$", re.IGNORECASE)
TG_DEEP_LINK_PATTERN = re.compile(r"^tg://user\?id=(?P<id>\d+)$", re.IGNORECASE)


def normalize_phone(phone: Optional[str]) -> str:
    if not phone:
        return ""
    digits = PHONE_CLEAN_PATTERN.sub("", str(phone))
    if not digits:
        return ""
    if digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) > 11:
        digits = digits[-11:]
    return digits


def normalize_username(username: Optional[str]) -> str:
    if not username:
        return ""
    value = str(username).strip()
    if value.startswith("@"):
        value = value[1:]
    return value.lower()


def _safe_tg_id(raw: str | None) -> int | None:
    if not raw:
        return None
    value = str(raw).strip()
    if not value.isdigit():
        return None
    try:
        tg_id = int(value)
    except (TypeError, ValueError):
        return None
    if tg_id <= 0:
        return None
    return tg_id


def parse_telegram_reference(value: Optional[str]) -> Tuple[str, str, int | None]:
    """
    Return (username_key, phone_key, telegram_id).
    username_key: normalized username without '@'
    phone_key: normalized phone if reference contains phone-like value
    telegram_id: positive integer id if reference is explicit tg id
    """
    if not value:
        return "", "", None

    raw = str(value).strip()
    deep_link_match = TG_DEEP_LINK_PATTERN.match(raw)
    if deep_link_match:
        tg_id = _safe_tg_id(deep_link_match.group("id"))
        return "", "", tg_id

    url_match = TG_URL_PATTERN.match(raw)
    if url_match:
        raw = url_match.group("value")

    if raw.startswith("@"):
        raw = raw[1:]

    if raw.startswith("+"):
        phone = normalize_phone(raw)
        return "", phone, None

    if raw.isdigit():
        phone = normalize_phone(raw)
        tg_id = _safe_tg_id(raw)
        return "", phone, tg_id

    username = normalize_username(raw)
    return username, "", None


def telegram_url_from_username(username: str | None) -> str:
    normalized = normalize_username(username)
    return f"https://t.me/{normalized}" if normalized else ""


def normalize_person_telegram_fields(
    *,
    telegram: str | None,
    telegram_id: int | None,
) -> tuple[str, int | None]:
    username_key, _, parsed_id = parse_telegram_reference(telegram)
    normalized_id = telegram_id or parsed_id

    normalized_telegram = ""
    if username_key:
        normalized_telegram = telegram_url_from_username(username_key)

    return normalized_telegram, normalized_id
