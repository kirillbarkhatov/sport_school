from __future__ import annotations

import hashlib
import html
import logging
from dataclasses import dataclass
from urllib.parse import urlsplit

from asgiref.sync import async_to_sync
from django.conf import settings
from django.db.models import Max
from django.urls import reverse
from django.db import transaction
from django.utils import timezone
from time import perf_counter
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError

from api.models import PublicStreamAccess, StreamRun, WebhookEvent
from api.telemetry import log_event
from bot.models import TelegramChat

logger = logging.getLogger(__name__)

TABLE_EVENT_TYPES = {"group_table_updated", "group_completed"}
FINISHER_EVENT_TYPES = {"result_updated", "group_table_updated"}
SUPPORTED_EVENT_TYPES = TABLE_EVENT_TYPES | FINISHER_EVENT_TYPES


@dataclass(frozen=True)
class GroupTablePayload:
    group_key: str
    group_name: str
    run_stage: int
    rows: list[dict[str, object]]
    message_text: str
    message_hash: str


@dataclass(frozen=True)
class FinisherPayload:
    message_text: str
    message_hash: str


@dataclass(frozen=True)
class LinkPayload:
    message_text: str
    message_hash: str


def publish_stream_event_to_telegram(run: StreamRun, event: WebhookEvent) -> None:
    started = perf_counter()
    log_event(
        "tg_publish_raw_started",
        run_id=run.id,
        stream_id=run.stream_id,
        event_id=event.id,
        event_type=event.event_type,
    )
    run = StreamRun.objects.select_related("telegram_channel").filter(pk=run.pk).first()
    if run is None:
        return
    if not run.telegram_publish_enabled or run.telegram_channel_id is None:
        return
    if not settings.BOT_TOKEN:
        _set_last_error(run, "BOT_TOKEN is not configured")
        return

    payload_wrapper = event.payload_json if isinstance(event.payload_json, dict) else {}
    payload = payload_wrapper.get("payload")
    if not isinstance(payload, dict):
        return

    event_type = (event.event_type or str(payload_wrapper.get("event_type") or "")).strip()
    if event_type not in SUPPORTED_EVENT_TYPES:
        log_event(
            "tg_publish_raw_skipped",
            reason="unsupported_event_type",
            run_id=run.id,
            stream_id=run.stream_id,
            event_id=event.id,
            event_type=event_type,
        )
        return

    bot = Bot(token=settings.BOT_TOKEN)
    table_created_new = False
    finisher_created_new = False

    if event_type in TABLE_EVENT_TYPES:
        table_payload = _build_group_table_payload(payload)
        if table_payload is not None:
            table_created_new = _publish_table_message(run=run, bot=bot, table_payload=table_payload)

    if event_type == "group_completed":
        # Keep completed table in history, but rotate the "current cycle" tail posts.
        _reset_cycle_tail_messages(run=run, bot=bot, reset_table_state=True)
        _clear_last_error(run)
        return

    if event_type in FINISHER_EVENT_TYPES:
        finisher_payload = _build_finisher_payload(run=run)
        if finisher_payload is None and table_created_new:
            finisher_payload = _placeholder_finisher_payload()
        if finisher_payload is not None:
            finisher_created_new = _publish_finisher_message(
                run=run,
                bot=bot,
                finisher_payload=finisher_payload,
            )

    if table_created_new or finisher_created_new or run.telegram_link_message_id is None:
        link_payload = _build_link_payload(run=run)
        if link_payload is not None:
            _publish_link_message(
                run=run,
                bot=bot,
                link_payload=link_payload,
                force_new=(table_created_new or finisher_created_new),
            )
    log_event(
        "tg_publish_raw_finished",
        run_id=run.id,
        stream_id=run.stream_id,
        event_id=event.id,
        event_type=event.event_type,
        duration_ms=int((perf_counter() - started) * 1000),
    )


def enable_stream_telegram_publication(run: StreamRun, channel_id: int) -> None:
    cleanup_chat_id = None
    cleanup_message_ids: list[int] = []
    with transaction.atomic():
        locked_run = StreamRun.objects.select_for_update().filter(pk=run.pk).first()
        if locked_run is None:
            return
        channel = TelegramChat.objects.filter(pk=channel_id).first()
        if channel is None:
            raise ValueError("Telegram channel not found")
        previous_channel_id = int(locked_run.telegram_channel_id or 0)
        if previous_channel_id:
            cleanup_chat_id = int(locked_run.telegram_channel.chat_id)
            for msg_id in (locked_run.telegram_finisher_message_id, locked_run.telegram_link_message_id):
                if msg_id is not None:
                    cleanup_message_ids.append(int(msg_id))
        last_event_id = (
            WebhookEvent.objects.filter(stream_id=locked_run.stream_id).aggregate(last_id=Max("id")).get("last_id")
        )
        locked_run.telegram_publish_enabled = True
        locked_run.telegram_channel = channel
        locked_run.telegram_resume_from_event_id = int(last_event_id or 0)
        locked_run.telegram_active_message_id = None
        locked_run.telegram_active_group_key = ""
        locked_run.telegram_active_run_stage = None
        locked_run.telegram_last_message_hash = ""
        locked_run.telegram_finisher_message_id = None
        locked_run.telegram_finisher_last_hash = ""
        locked_run.telegram_link_message_id = None
        locked_run.telegram_link_last_hash = ""
        locked_run.telegram_last_error = ""
        locked_run.save(
            update_fields=[
                "telegram_publish_enabled",
                "telegram_channel",
                "telegram_resume_from_event_id",
                "telegram_active_message_id",
                "telegram_active_group_key",
                "telegram_active_run_stage",
                "telegram_last_message_hash",
                "telegram_finisher_message_id",
                "telegram_finisher_last_hash",
                "telegram_link_message_id",
                "telegram_link_last_hash",
                "telegram_last_error",
                "updated_at",
            ]
        )

        run_id = locked_run.id
        transaction.on_commit(lambda: _publish_bootstrap_state_for_run_id(run_id))
        log_event(
            "tg_enabled",
            run_id=locked_run.id,
            stream_id=locked_run.stream_id,
            channel_id=channel_id,
            resume_from_event_id=locked_run.telegram_resume_from_event_id or 0,
        )
    if settings.BOT_TOKEN and cleanup_chat_id is not None and cleanup_message_ids:
        bot = Bot(token=settings.BOT_TOKEN)
        for msg_id in cleanup_message_ids:
            _delete_message_safe(bot=bot, chat_id=cleanup_chat_id, message_id=msg_id)


def disable_stream_telegram_publication(run: StreamRun) -> None:
    cleanup_chat_id = None
    cleanup_message_ids: list[int] = []
    with transaction.atomic():
        locked_run = StreamRun.objects.select_for_update().filter(pk=run.pk).first()
        if locked_run is None:
            return
        if locked_run.telegram_channel_id is not None:
            cleanup_chat_id = int(locked_run.telegram_channel.chat_id)
        for msg_id in (
            locked_run.telegram_active_message_id,
            locked_run.telegram_finisher_message_id,
            locked_run.telegram_link_message_id,
        ):
            if msg_id is not None:
                cleanup_message_ids.append(int(msg_id))

        locked_run.telegram_publish_enabled = False
        locked_run.telegram_channel = None
        locked_run.telegram_resume_from_event_id = None
        locked_run.telegram_active_message_id = None
        locked_run.telegram_active_group_key = ""
        locked_run.telegram_active_run_stage = None
        locked_run.telegram_last_message_hash = ""
        locked_run.telegram_finisher_message_id = None
        locked_run.telegram_finisher_last_hash = ""
        locked_run.telegram_link_message_id = None
        locked_run.telegram_link_last_hash = ""
        locked_run.telegram_last_error = ""
        locked_run.save(
            update_fields=[
                "telegram_publish_enabled",
                "telegram_channel",
                "telegram_resume_from_event_id",
                "telegram_active_message_id",
                "telegram_active_group_key",
                "telegram_active_run_stage",
                "telegram_last_message_hash",
                "telegram_finisher_message_id",
                "telegram_finisher_last_hash",
                "telegram_link_message_id",
                "telegram_link_last_hash",
                "telegram_last_error",
                "updated_at",
            ]
        )

    if settings.BOT_TOKEN and cleanup_chat_id is not None and cleanup_message_ids:
        bot = Bot(token=settings.BOT_TOKEN)
        for msg_id in cleanup_message_ids:
            _delete_message_safe(bot=bot, chat_id=cleanup_chat_id, message_id=msg_id)
    log_event(
        "tg_disabled",
        run_id=run.id,
        stream_id=run.stream_id,
        deleted_messages=len(cleanup_message_ids),
    )


def _send_message(bot: Bot, **kwargs):
    return async_to_sync(_send_message_async)(token=bot.token, kwargs=kwargs)


def _edit_message(bot: Bot, **kwargs):
    return async_to_sync(_edit_message_async)(token=bot.token, kwargs=kwargs)


def _delete_message(bot: Bot, **kwargs):
    return async_to_sync(_delete_message_async)(token=bot.token, kwargs=kwargs)


async def _send_message_async(*, token: str, kwargs: dict[str, object]):
    async with Bot(token=token) as client:
        return await client.send_message(**kwargs)


async def _edit_message_async(*, token: str, kwargs: dict[str, object]):
    async with Bot(token=token) as client:
        return await client.edit_message_text(**kwargs)


async def _delete_message_async(*, token: str, kwargs: dict[str, object]):
    async with Bot(token=token) as client:
        return await client.delete_message(**kwargs)


def _publish_table_message(*, run: StreamRun, bot: Bot, table_payload: GroupTablePayload) -> bool:
    key_changed = (
        run.telegram_active_group_key != table_payload.group_key
        or int(run.telegram_active_run_stage or 0) != table_payload.run_stage
    )
    chat_id = run.telegram_channel.chat_id

    if (
        not key_changed
        and run.telegram_active_message_id is not None
        and run.telegram_last_message_hash == table_payload.message_hash
    ):
        return False

    if key_changed:
        # On active group switch, keep previous table post as history and rotate tail posts.
        _reset_cycle_tail_messages(run=run, bot=bot, reset_table_state=False)

    if key_changed or run.telegram_active_message_id is None:
        sent_message = _send_message(
            bot,
            chat_id=chat_id,
            text=table_payload.message_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        _update_table_state(
            run,
            message_id=int(sent_message.message_id),
            group_key=table_payload.group_key,
            run_stage=table_payload.run_stage,
            message_hash=table_payload.message_hash,
        )
        _clear_last_error(run)
        return True

    try:
        _edit_message(
            bot,
            chat_id=chat_id,
            message_id=int(run.telegram_active_message_id),
            text=table_payload.message_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        _update_table_state(
            run,
            message_id=int(run.telegram_active_message_id),
            group_key=table_payload.group_key,
            run_stage=table_payload.run_stage,
            message_hash=table_payload.message_hash,
        )
        _clear_last_error(run)
        return False
    except BadRequest as exc:
        lowered = str(exc).lower()
        if "message is not modified" in lowered:
            _update_table_state(
                run,
                message_id=int(run.telegram_active_message_id),
                group_key=table_payload.group_key,
                run_stage=table_payload.run_stage,
                message_hash=table_payload.message_hash,
            )
            _clear_last_error(run)
            return False
        if "message to edit not found" in lowered:
            sent_message = _send_message(
                bot,
                chat_id=chat_id,
                text=table_payload.message_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            _update_table_state(
                run,
                message_id=int(sent_message.message_id),
                group_key=table_payload.group_key,
                run_stage=table_payload.run_stage,
                message_hash=table_payload.message_hash,
            )
            _clear_last_error(run)
            return True
        _set_last_error(run, str(exc))
        raise
    except TelegramError as exc:
        _set_last_error(run, str(exc))
        raise


def _build_group_table_payload(payload: dict[str, object]) -> GroupTablePayload | None:
    group_key = str(payload.get("group_key") or "").strip()
    group_name = str(payload.get("group_name") or "").strip()

    data = payload.get("data")
    if isinstance(data, dict) and isinstance(data.get("group_table"), dict):
        data = data.get("group_table")
    if not isinstance(data, dict):
        return None

    rows_raw = data.get("rows")
    if not isinstance(rows_raw, list) or not rows_raw:
        return None

    rows: list[dict[str, object]] = [row for row in rows_raw if isinstance(row, dict)]
    if not rows:
        return None

    if not group_key:
        group_key = str(data.get("group_key") or "").strip()
    if not group_key:
        return None
    if not group_name:
        group_name = str(data.get("group_name") or "").strip()

    run_stage = _detect_run_stage(payload=payload, rows=rows)
    message_text = _render_table_message(group_name=group_name, run_stage=run_stage, rows=rows)
    message_hash = hashlib.sha256(message_text.encode("utf-8")).hexdigest()
    return GroupTablePayload(
        group_key=group_key,
        group_name=group_name,
        run_stage=run_stage,
        rows=rows,
        message_text=message_text,
        message_hash=message_hash,
    )


def _detect_run_stage(*, payload: dict[str, object], rows: list[dict[str, object]]) -> int:
    explicit_stage = int(payload.get("run_stage") or 0)
    if explicit_stage in {1, 2}:
        return explicit_stage
    for row in rows:
        run2 = str(row.get("run2") or "").strip()
        if run2 and run2 != "-":
            return 2
    return 1


def _render_table_message(*, group_name: str, run_stage: int, rows: list[dict[str, object]]) -> str:
    # Compact fixed-width view for Telegram channel posts.
    limits = (2, 3, 5, 5, 5, 7, 5)
    headers = ("Мс", "Ст№", "ФамИ", "Run 1", "Run 2", "Итог", "Инт.")
    table_rows: list[tuple[str, ...]] = []
    for row in rows:
        table_rows.append(
            (
                _normalize(_clip(str(row.get("place") or "-"), limits[0]), limits[0], align="right"),
                _normalize(_clip(str(row.get("start_number") or "-"), limits[1]), limits[1], align="right"),
                _normalize(_clip(_short_name(str(row.get("full_name") or "")), limits[2]), limits[2], align="left"),
                _normalize(_clip(str(row.get("run1") or "-"), limits[3]), limits[3], align="right"),
                _normalize(_clip(str(row.get("run2") or "-"), limits[4]), limits[4], align="right"),
                _normalize(_clip(str(row.get("total") or "-"), limits[5]), limits[5], align="right"),
                _normalize(_clip(str(row.get("interval") or "-"), limits[6]), limits[6], align="right"),
            )
        )
    header_cells = tuple(
        _normalize(header, limit, align="left")
        for header, limit in zip(headers, limits, strict=False)
    )

    def _fmt(cells: tuple[str, ...]) -> str:
        return "|" + "|".join(cells) + "|"

    title = f"{group_name or 'Группа'} | заезд {run_stage}"
    lines = [title, _fmt(header_cells), "|" + "|".join("-" * limit for limit in limits) + "|"]
    for cells in table_rows:
        lines.append(_fmt(cells))
    return f"<pre>{html.escape(chr(10).join(lines))}</pre>"


def _short_name(full_name: str) -> str:
    parts = [part for part in full_name.strip().split() if part]
    if not parts:
        return "-"
    surname = parts[0][:4]
    name_initial = parts[1][0] if len(parts) > 1 and parts[1] else ""
    display = f"{surname}{name_initial}".strip()
    return display or surname


def _clip(value: str, max_len: int) -> str:
    text = (value or "").strip()
    if not text:
        return "-"
    if len(text) <= max_len:
        return text
    return text[:max_len]


def _normalize(value: str, width: int, *, align: str) -> str:
    if align == "right":
        return value.rjust(width)
    return value.ljust(width)


def _update_table_state(run: StreamRun, *, message_id: int, group_key: str, run_stage: int, message_hash: str) -> None:
    run.telegram_active_message_id = message_id
    run.telegram_active_group_key = group_key
    run.telegram_active_run_stage = run_stage
    run.telegram_last_message_hash = message_hash
    run.save(
        update_fields=[
            "telegram_active_message_id",
            "telegram_active_group_key",
            "telegram_active_run_stage",
            "telegram_last_message_hash",
            "updated_at",
        ]
    )


def _clear_table_state(run: StreamRun) -> None:
    run.telegram_active_message_id = None
    run.telegram_active_group_key = ""
    run.telegram_active_run_stage = None
    run.telegram_last_message_hash = ""
    run.save(
        update_fields=[
            "telegram_active_message_id",
            "telegram_active_group_key",
            "telegram_active_run_stage",
            "telegram_last_message_hash",
            "updated_at",
        ]
    )


def _build_finisher_payload(*, run: StreamRun) -> FinisherPayload | None:
    if not isinstance(run.external_response_json, dict):
        return None
    stream_output = run.external_response_json.get("stream_output")
    if not isinstance(stream_output, dict):
        return None
    last_result_data = stream_output.get("last_result_data")
    if not isinstance(last_result_data, dict):
        return None
    result_event_id = int(stream_output.get("last_result_event_id") or 0)
    resume_from_event_id = int(run.telegram_resume_from_event_id or 0)
    if resume_from_event_id and result_event_id and result_event_id < resume_from_event_id:
        return None
    updated_results = last_result_data.get("updated_results")
    if not isinstance(updated_results, list) or not updated_results:
        return None

    last_item = None
    for item in reversed(updated_results):
        if isinstance(item, dict):
            last_item = item
            break
    if last_item is None:
        return None

    full_name = str(last_item.get("full_name") or "-").strip() or "-"
    start_number = str(last_item.get("start_number") or "").strip()
    club = str(last_item.get("club") or "-").strip() or "-"
    run1 = str(last_item.get("run1") or "-").strip() or "-"
    run2 = str(last_item.get("run2") or "-").strip() or "-"
    last_run_time = run2 if run2 and run2 != "-" else run1

    finisher_title = f"Финиш: Ст. № {start_number} {full_name}" if start_number else f"Финиш: {full_name}"
    lines = [
        finisher_title,
        f"Клуб: {club}",
        last_run_time or "-",
    ]
    message_text = f"<pre>{html.escape(chr(10).join(lines))}</pre>"
    message_hash = hashlib.sha256(message_text.encode("utf-8")).hexdigest()
    return FinisherPayload(message_text=message_text, message_hash=message_hash)


def _placeholder_finisher_payload() -> FinisherPayload:
    lines = ["Финиш: —", "Клуб: —", "—"]
    message_text = f"<pre>{html.escape(chr(10).join(lines))}</pre>"
    message_hash = hashlib.sha256(message_text.encode("utf-8")).hexdigest()
    return FinisherPayload(message_text=message_text, message_hash=message_hash)


def _publish_finisher_message(
    *,
    run: StreamRun,
    bot: Bot,
    finisher_payload: FinisherPayload,
) -> bool:
    chat_id = run.telegram_channel.chat_id
    if (
        run.telegram_finisher_message_id is not None
        and run.telegram_finisher_last_hash == finisher_payload.message_hash
    ):
        return False

    if run.telegram_finisher_message_id is None:
        sent = _send_message(
            bot,
            chat_id=chat_id,
            text=finisher_payload.message_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        _update_finisher_state(run, message_id=int(sent.message_id), message_hash=finisher_payload.message_hash)
        _clear_last_error(run)
        return True

    try:
        _edit_message(
            bot,
            chat_id=chat_id,
            message_id=int(run.telegram_finisher_message_id),
            text=finisher_payload.message_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        _update_finisher_state(
            run,
            message_id=int(run.telegram_finisher_message_id),
            message_hash=finisher_payload.message_hash,
        )
        _clear_last_error(run)
        return False
    except BadRequest as exc:
        lowered = str(exc).lower()
        if "message is not modified" in lowered:
            _update_finisher_state(
                run,
                message_id=int(run.telegram_finisher_message_id),
                message_hash=finisher_payload.message_hash,
            )
            _clear_last_error(run)
            return False
        if "message to edit not found" in lowered:
            sent = _send_message(
                bot,
                chat_id=chat_id,
                text=finisher_payload.message_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
            _update_finisher_state(run, message_id=int(sent.message_id), message_hash=finisher_payload.message_hash)
            _clear_last_error(run)
            return True
        _set_last_error(run, str(exc))
        raise
    except TelegramError as exc:
        _set_last_error(run, str(exc))
        raise


def _update_finisher_state(run: StreamRun, *, message_id: int, message_hash: str) -> None:
    run.telegram_finisher_message_id = message_id
    run.telegram_finisher_last_hash = message_hash
    run.save(
        update_fields=[
            "telegram_finisher_message_id",
            "telegram_finisher_last_hash",
            "updated_at",
        ]
    )


def _clear_finisher_state(run: StreamRun) -> None:
    run.telegram_finisher_message_id = None
    run.telegram_finisher_last_hash = ""
    run.save(update_fields=["telegram_finisher_message_id", "telegram_finisher_last_hash", "updated_at"])


def _build_link_payload(*, run: StreamRun) -> LinkPayload | None:
    access = (
        PublicStreamAccess.objects.filter(stream_run=run, is_active=True, expires_at__gt=timezone.now())
        .order_by("-created_at")
        .first()
    )
    if access is None:
        return None
    path = reverse("online-results-live-public", kwargs={"token": access.token})
    site_base = str(getattr(settings, "SITE_BASE_URL", "") or "").strip().rstrip("/")
    public_base = str(getattr(settings, "ONLINE_RESULTS_WEBHOOK_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")

    base_url = site_base
    if _is_local_base_url(base_url) and public_base and not _is_local_base_url(public_base):
        base_url = public_base
    if not base_url:
        base_url = public_base
    if not base_url:
        return None
    url = f"{base_url}{path}"
    safe_url = html.escape(url, quote=True)
    text = f'<a href="{safe_url}">Онлайн-результаты: открыть протокол</a>'
    message_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return LinkPayload(message_text=text, message_hash=message_hash)


def _is_local_base_url(url: str) -> bool:
    parsed = urlsplit((url or "").strip())
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "host.docker.internal"}


def _publish_link_message(*, run: StreamRun, bot: Bot, link_payload: LinkPayload, force_new: bool) -> bool:
    chat_id = run.telegram_channel.chat_id
    if not force_new and run.telegram_link_message_id is not None and run.telegram_link_last_hash == link_payload.message_hash:
        return False

    if force_new and run.telegram_link_message_id is not None:
        _delete_message_safe(bot=bot, chat_id=chat_id, message_id=int(run.telegram_link_message_id))
        _clear_link_state(run)

    if run.telegram_link_message_id is None:
        sent = _send_message(
            bot,
            chat_id=chat_id,
            text=link_payload.message_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
        _update_link_state(run, message_id=int(sent.message_id), message_hash=link_payload.message_hash)
        _clear_last_error(run)
        return True

    try:
        _edit_message(
            bot,
            chat_id=chat_id,
            message_id=int(run.telegram_link_message_id),
            text=link_payload.message_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=False,
        )
        _update_link_state(
            run,
            message_id=int(run.telegram_link_message_id),
            message_hash=link_payload.message_hash,
        )
        _clear_last_error(run)
        return False
    except BadRequest as exc:
        lowered = str(exc).lower()
        if "message is not modified" in lowered:
            _update_link_state(
                run,
                message_id=int(run.telegram_link_message_id),
                message_hash=link_payload.message_hash,
            )
            _clear_last_error(run)
            return False
        if "message to edit not found" in lowered:
            sent = _send_message(
                bot,
                chat_id=chat_id,
                text=link_payload.message_text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=False,
            )
            _update_link_state(run, message_id=int(sent.message_id), message_hash=link_payload.message_hash)
            _clear_last_error(run)
            return True
        _set_last_error(run, str(exc))
        raise
    except TelegramError as exc:
        _set_last_error(run, str(exc))
        raise


def _update_link_state(run: StreamRun, *, message_id: int, message_hash: str) -> None:
    run.telegram_link_message_id = message_id
    run.telegram_link_last_hash = message_hash
    run.save(update_fields=["telegram_link_message_id", "telegram_link_last_hash", "updated_at"])


def _clear_link_state(run: StreamRun) -> None:
    run.telegram_link_message_id = None
    run.telegram_link_last_hash = ""
    run.save(update_fields=["telegram_link_message_id", "telegram_link_last_hash", "updated_at"])


def _delete_message_safe(*, bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        _delete_message(bot, chat_id=chat_id, message_id=message_id)
    except TelegramError:
        logger.info("telegram_delete_message_failed chat_id=%s message_id=%s", chat_id, message_id)


def _reset_cycle_tail_messages(*, run: StreamRun, bot: Bot, reset_table_state: bool) -> None:
    chat_id = run.telegram_channel.chat_id
    if run.telegram_finisher_message_id is not None:
        _delete_message_safe(bot=bot, chat_id=chat_id, message_id=int(run.telegram_finisher_message_id))
    if run.telegram_link_message_id is not None:
        _delete_message_safe(bot=bot, chat_id=chat_id, message_id=int(run.telegram_link_message_id))

    _clear_finisher_state(run)
    _clear_link_state(run)
    if reset_table_state:
        _clear_table_state(run)


def _set_last_error(run: StreamRun, error_text: str) -> None:
    run.telegram_last_error = (error_text or "")[:1000]
    run.save(update_fields=["telegram_last_error", "updated_at"])


def _clear_last_error(run: StreamRun) -> None:
    if run.telegram_last_error:
        run.telegram_last_error = ""
        run.save(update_fields=["telegram_last_error", "updated_at"])


def _publish_bootstrap_state(*, run: StreamRun) -> None:
    if not run.telegram_publish_enabled or run.telegram_channel_id is None:
        return
    if not settings.BOT_TOKEN:
        _set_last_error(run, "BOT_TOKEN is not configured")
        return
    stream_output = run.external_response_json.get("stream_output") if isinstance(run.external_response_json, dict) else {}
    if not isinstance(stream_output, dict):
        stream_output = {}
    table_payload = _build_bootstrap_group_table_payload(stream_output=stream_output)
    bot = Bot(token=settings.BOT_TOKEN)
    created_any = False
    if table_payload is not None:
        created_any = _publish_table_message(run=run, bot=bot, table_payload=table_payload) or created_any
    finisher_payload = _placeholder_finisher_payload()
    created_any = _publish_finisher_message(run=run, bot=bot, finisher_payload=finisher_payload) or created_any
    link_payload = _build_link_payload(run=run)
    if link_payload is not None:
        _publish_link_message(run=run, bot=bot, link_payload=link_payload, force_new=created_any)


def _publish_bootstrap_state_for_run_id(run_id: int) -> None:
    run = StreamRun.objects.filter(pk=run_id).select_related("telegram_channel").first()
    if run is None:
        return
    _publish_bootstrap_state(run=run)


def _build_bootstrap_group_table_payload(*, stream_output: dict[str, object]) -> GroupTablePayload | None:
    latest_group_tables = stream_output.get("latest_group_tables")
    if not isinstance(latest_group_tables, dict) or not latest_group_tables:
        return None
    current_group_key = str(stream_output.get("current_group_key") or "").strip()
    candidates: list[tuple[str, dict[str, object]]] = []
    if current_group_key and isinstance(latest_group_tables.get(current_group_key), dict):
        candidates.append((current_group_key, latest_group_tables.get(current_group_key)))
    for key, value in latest_group_tables.items():
        if key == current_group_key:
            continue
        if isinstance(value, dict):
            candidates.append((str(key), value))
    for group_key, block in candidates:
        data = block.get("data")
        if isinstance(data, dict) and isinstance(data.get("group_table"), dict):
            data = data.get("group_table")
        if not isinstance(data, dict):
            continue
        rows = data.get("rows")
        if not isinstance(rows, list) or not rows:
            continue
        payload = {
            "group_key": group_key,
            "group_name": str(block.get("group_name") or data.get("group_name") or ""),
            "run_stage": int(block.get("run_stage") or 0),
            "data": data,
        }
        table_payload = _build_group_table_payload(payload)
        if table_payload is not None:
            return table_payload
    return None
