from __future__ import annotations

import hashlib
import html
import logging
from dataclasses import dataclass

from asgiref.sync import async_to_sync
from django.conf import settings
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError

from api.models import StreamRun, WebhookEvent

logger = logging.getLogger(__name__)

TABLE_EVENT_TYPES = {"group_table_updated", "group_completed"}
FINISHER_EVENT_TYPES = {"result_updated", "group_table_updated", "group_completed"}
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


def publish_stream_event_to_telegram(run: StreamRun, event: WebhookEvent) -> None:
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
        return

    bot = Bot(token=settings.BOT_TOKEN)
    table_sent_new = False
    if event_type in TABLE_EVENT_TYPES:
        table_payload = _build_group_table_payload(payload)
        if table_payload is not None:
            table_sent_new = _publish_table_message(run=run, bot=bot, table_payload=table_payload)

    if event_type in FINISHER_EVENT_TYPES:
        finisher_payload = _build_finisher_payload(run=run)
        if finisher_payload is not None:
            _publish_finisher_message(
                run=run,
                bot=bot,
                finisher_payload=finisher_payload,
                force_new=table_sent_new,
            )


def _send_message(bot: Bot, **kwargs):
    return async_to_sync(_send_message_async)(token=bot.token, kwargs=kwargs)


def _edit_message(bot: Bot, **kwargs):
    return async_to_sync(_edit_message_async)(token=bot.token, kwargs=kwargs)


async def _send_message_async(*, token: str, kwargs: dict[str, object]):
    async with Bot(token=token) as client:
        return await client.send_message(**kwargs)


async def _edit_message_async(*, token: str, kwargs: dict[str, object]):
    async with Bot(token=token) as client:
        return await client.edit_message_text(**kwargs)


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


def _build_finisher_payload(*, run: StreamRun) -> FinisherPayload | None:
    if not isinstance(run.external_response_json, dict):
        return None
    stream_output = run.external_response_json.get("stream_output")
    if not isinstance(stream_output, dict):
        return None
    last_result_data = stream_output.get("last_result_data")
    if not isinstance(last_result_data, dict):
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


def _publish_finisher_message(
    *,
    run: StreamRun,
    bot: Bot,
    finisher_payload: FinisherPayload,
    force_new: bool,
) -> None:
    chat_id = run.telegram_channel.chat_id
    if (
        not force_new
        and run.telegram_finisher_message_id is not None
        and run.telegram_finisher_last_hash == finisher_payload.message_hash
    ):
        return

    if force_new or run.telegram_finisher_message_id is None:
        sent = _send_message(
            bot,
            chat_id=chat_id,
            text=finisher_payload.message_text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        _update_finisher_state(run, message_id=int(sent.message_id), message_hash=finisher_payload.message_hash)
        _clear_last_error(run)
        return

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
    except BadRequest as exc:
        lowered = str(exc).lower()
        if "message is not modified" in lowered:
            _update_finisher_state(
                run,
                message_id=int(run.telegram_finisher_message_id),
                message_hash=finisher_payload.message_hash,
            )
            _clear_last_error(run)
            return
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
            return
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


def _set_last_error(run: StreamRun, error_text: str) -> None:
    run.telegram_last_error = (error_text or "")[:1000]
    run.save(update_fields=["telegram_last_error", "updated_at"])


def _clear_last_error(run: StreamRun) -> None:
    if run.telegram_last_error:
        run.telegram_last_error = ""
        run.save(update_fields=["telegram_last_error", "updated_at"])
