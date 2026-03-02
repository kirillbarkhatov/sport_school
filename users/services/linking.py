from __future__ import annotations

import dataclasses
import re
from collections import defaultdict
from typing import Iterable, Optional, Sequence, Tuple

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import transaction
from django.utils import timezone
from telegram import User as TelegramUser

from members.models import PersonMergeRedirect
from school.models import Person
from users.constants import (
    ADMIN_GROUP_NAME,
    COACH_GROUP_NAME,
    MANAGER_GROUP_NAME,
)
from users.models import User, UserPersonLink, UserPersonLinkStatus

PHONE_CLEAN_PATTERN = re.compile(r"\D+")
TELEGRAM_URL_PATTERN = re.compile(r"https?://t\.me/(?P<value>[\w@+\d_]+)", re.IGNORECASE)


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
    if digits.startswith("+"):
        digits = digits[1:]
    if len(digits) > 11:
        digits = digits[-11:]
    return digits


def _normalize_username(username: Optional[str]) -> str:
    if not username:
        return ""
    return str(username).strip().lstrip("@").lower()


def _split_telegram_reference(value: Optional[str]) -> Tuple[str, str]:
    if not value:
        return "", ""

    raw = value.strip()
    match = TELEGRAM_URL_PATTERN.match(raw)
    if match:
        raw = match.group("value")

    if raw.startswith("@"):
        raw = raw[1:]

    if raw.startswith("+"):
        phone = normalize_phone(raw)
        return "", phone

    if raw.startswith("http"):
        return "", ""

    if raw.isdigit():
        return "", normalize_phone(raw)

    return raw.lower(), ""


@dataclasses.dataclass(slots=True)
class UserTelegramProfile:
    tg_id: Optional[int]
    username: str
    first_name: str
    last_name: str
    phone: str

    @classmethod
    def from_telegram(cls, tg_user: TelegramUser) -> "UserTelegramProfile":
        return cls(
            tg_id=getattr(tg_user, "id", None),
            username=_normalize_username(getattr(tg_user, "username", "")),
            first_name=getattr(tg_user, "first_name", "") or "",
            last_name=getattr(tg_user, "last_name", "") or "",
            phone=normalize_phone(getattr(tg_user, "phone_number", "")),
        )

    def iter_candidate_telegram_keys(self) -> Sequence[str]:
        candidates: list[str] = []
        if self.username:
            candidates.append(self.username)
        if self.phone:
            candidates.append(self.phone)
        return candidates


def _ensure_person_telegram_link(user: User, profile: UserTelegramProfile) -> None:
    person = getattr(user, "person", None)
    if not person:
        return

    username = profile.username or _normalize_username(user.tg_username)
    if not username:
        return

    desired_url = f"https://t.me/{username}"

    current_value = (person.telegram or "").strip()
    if current_value:
        current_username, _ = _split_telegram_reference(current_value)
        if current_username == username:
            return

    person.telegram = desired_url
    person.save(update_fields=["telegram"])


@transaction.atomic
def update_user_from_telegram(user: User, profile: UserTelegramProfile) -> User:
    updated_fields: list[str] = []

    if profile.first_name and user.first_name != profile.first_name:
        user.first_name = profile.first_name
        updated_fields.append("first_name")

    if profile.last_name and user.last_name != profile.last_name:
        user.last_name = profile.last_name
        updated_fields.append("last_name")

    if profile.phone and normalize_phone(user.phone) != profile.phone:
        user.phone = profile.phone
        updated_fields.append("phone")

    if profile.username and _normalize_username(user.tg_username) != profile.username:
        user.tg_username = profile.username
        updated_fields.append("tg_username")

    if user.tg_first_name != profile.first_name:
        user.tg_first_name = profile.first_name
        if "tg_first_name" not in updated_fields:
            updated_fields.append("tg_first_name")

    if user.tg_last_name != profile.last_name:
        user.tg_last_name = profile.last_name
        if "tg_last_name" not in updated_fields:
            updated_fields.append("tg_last_name")

    if user.tg_id != profile.tg_id and profile.tg_id:
        user.tg_id = profile.tg_id
        updated_fields.append("tg_id")

    if updated_fields:
        user.save(update_fields=updated_fields)

    user.mark_bot_interaction()
    _ensure_person_telegram_link(user, profile)
    return user


def _collect_person_candidates(
    surname: str,
    phone: str,
    telegram_keys: Iterable[str],
) -> dict[int, set[str]]:
    matches: dict[int, set[str]] = defaultdict(set)
    redirected_sources = PersonMergeRedirect.objects.filter(is_active=True).values_list("source_person_id", flat=True)

    if surname:
        surname_matches = (
            Person.objects.exclude(id__in=redirected_sources)
            .filter(surname__iexact=surname)
            .values_list("id", flat=True)
        )
        for person_id in surname_matches:
            matches[person_id].add("surname")

    if phone:
        candidate_phones = (
            Person.objects.exclude(id__in=redirected_sources)
            .exclude(phone__isnull=True)
            .exclude(phone="")
            .values_list("id", "phone")
        )
        for person_id, person_phone in candidate_phones:
            if normalize_phone(person_phone) == phone:
                matches[person_id].add("phone")

    keys = list(dict.fromkeys(k for k in telegram_keys if k))
    if keys:
        candidate_telegram = (
            Person.objects.exclude(id__in=redirected_sources)
            .exclude(telegram__isnull=True)
            .exclude(telegram="")
            .values_list("id", "telegram")
        )
        for person_id, person_telegram in candidate_telegram:
            username_key, phone_key = _split_telegram_reference(person_telegram)
            if username_key and username_key in keys:
                matches[person_id].add("telegram")
            if phone_key and phone_key in keys:
                matches[person_id].add("telegram")

    return matches


def find_best_person_match(user: User) -> Tuple[Optional[Person], list[str]]:
    surname = (user.last_name or user.tg_last_name or "").strip()
    phone = normalize_phone(user.phone)
    telegram_keys = []
    username = _normalize_username(user.tg_username)
    if username:
        telegram_keys.append(username)
    if phone:
        telegram_keys.append(phone)

    matches = _collect_person_candidates(surname, phone, telegram_keys)
    if not matches:
        return None, []

    best_person_id, reasons = max(
        matches.items(),
        key=lambda item: (len(item[1]), item[0]),
    )
    person = Person.objects.filter(pk=best_person_id).first()
    return person, sorted(reasons)


@transaction.atomic
def refresh_user_person_link(user: User, *, force: bool = False) -> Tuple[UserPersonLink, bool]:
    link, _ = UserPersonLink.objects.select_for_update().get_or_create(user=user)

    if link.status == UserPersonLinkStatus.APPROVED and not force:
        return link, False

    person, reasons = find_best_person_match(user)
    suggested_id = person.pk if person else None

    changed = False
    if link.suggested_person_id != suggested_id:
        link.suggested_person = person
        changed = True
    existing_reasons = sorted(link.matched_reasons or [])
    if existing_reasons != reasons:
        link.matched_reasons = reasons
        changed = True

    if changed or force:
        link.reset_to_pending()
        link.save(update_fields=[
            "suggested_person",
            "matched_reasons",
            "status",
            "decided_by",
            "decided_at",
            "decision_note",
            "updated_at",
        ])

    return link, changed


@transaction.atomic
def update_user_comment(user: User, comment: str) -> UserPersonLink:
    link, _ = UserPersonLink.objects.select_for_update().get_or_create(user=user)
    link.user_comment = comment.strip()
    link.user_comment_updated_at = timezone.now()
    link.reset_to_pending()
    link.save(update_fields=[
        "user_comment",
        "user_comment_updated_at",
        "status",
        "decided_by",
        "decided_at",
        "decision_note",
        "updated_at",
    ])
    return link


def _get_group(name: str) -> Group:
    group, _ = Group.objects.get_or_create(name=name)
    return group


def _env_id_set(raw_ids: Sequence[str]) -> set[str]:
    return {str(value).strip() for value in raw_ids if str(value).strip()}


def assign_groups_from_env(user: User) -> None:
    if not user.tg_id:
        return

    mappings = (
        (_env_id_set(getattr(settings, "TELEGRAM_ADMIN_IDS", []) or []), ADMIN_GROUP_NAME),
        (_env_id_set(getattr(settings, "TELEGRAM_COACH_IDS", []) or []), COACH_GROUP_NAME),
        (_env_id_set(getattr(settings, "TELEGRAM_MANAGER_IDS", []) or []), MANAGER_GROUP_NAME),
    )
    tg_id = str(user.tg_id)
    for id_set, group_name in mappings:
        if tg_id in id_set:
            group = _get_group(group_name)
            user.groups.add(group)


@transaction.atomic
def ensure_user_for_start(profile: UserTelegramProfile, token: Optional[str]) -> tuple[User, bool]:
    if not profile.tg_id:
        raise ValueError("Telegram profile must include tg_id")

    defaults = {
        "email": f"{profile.tg_id}@autogen.local",
        "tg_first_name": profile.first_name,
        "tg_last_name": profile.last_name,
        "tg_username": profile.username,
        "first_name": profile.first_name,
        "last_name": profile.last_name,
    }
    user, created = User.objects.get_or_create(
        tg_id=profile.tg_id,
        defaults=defaults,
    )

    user = update_user_from_telegram(user, profile)

    updated = False
    if token:
        if user.token != token:
            user.token = token
            updated = True
    if not user.email:
        user.email = f"{profile.tg_id}@autogen.local"
        updated = True
    if updated:
        user.save(update_fields=["token", "email"] if token else ["email"])

    assign_groups_from_env(user)
    link, _ = refresh_user_person_link(user)
    return user, created, link


@transaction.atomic
def sync_existing_user(profile: UserTelegramProfile) -> Optional[User]:
    if not profile.tg_id:
        return None
    user = User.objects.filter(tg_id=profile.tg_id).first()
    if not user:
        return None
    update_user_from_telegram(user, profile)
    assign_groups_from_env(user)
    link, _ = refresh_user_person_link(user)
    return user, link
