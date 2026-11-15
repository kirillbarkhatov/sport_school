"""Публикация трансляции в Telegram-канал.

В канале поддерживается «хвост» из трёх постов:
  1. таблица текущей группы и заезда — редактируется на месте;
  2. последний финишировавший — редактируется;
  3. ссылка на live-страницу.
При смене группы/заезда старая таблица остаётся в истории канала, а новая таблица, финиш и ссылка
публикуются заново (финиш и ссылка удаляются и пересоздаются, чтобы оставаться внизу).
"""

from __future__ import annotations

import hashlib
import html
import logging
from dataclasses import dataclass

from asgiref.sync import async_to_sync
from django.conf import settings
from django.urls import reverse
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError

from .models import ResultsEvent, TelegramPublication
from .state import GroupView, LiveState, build_state

logger = logging.getLogger(__name__)

COLUMNS = (("Мс", 2, "r"), ("Ст№", 3, "r"), ("ФамИ", 6, "l"), ("1 з.", 6, "r"), ("2 з.", 6, "r"), ("Итог", 7, "r"))


@dataclass(frozen=True)
class Post:
    text: str

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.text.encode()).hexdigest()


def short_name(full_name: str) -> str:
    parts = full_name.split()
    if not parts:
        return "—"
    return f"{parts[0][:5]}{parts[1][0] if len(parts) > 1 else ''}"


def _cell(value: str, width: int, align: str) -> str:
    value = (value or "—")[:width]
    return value.rjust(width) if align == "r" else value.ljust(width)


def render_table(group: GroupView, run_number: int) -> Post:
    columns = [c for c in COLUMNS if not (group.is_single_run and c[0] == "2 з.")]
    lines = [f"{group.name} | {'заезд ' + str(run_number) if not group.is_single_run else 'один заезд'}"]
    lines.append("|".join(_cell(title, width, "l") for title, width, _ in columns))
    lines.append("|".join("-" * width for _, width, _ in columns))
    for row in group.rows:
        values = {
            "Мс": "" if row.waiting else str(row.place),
            "Ст№": str(row.start_number),
            "ФамИ": short_name(row.full_name),
            "1 з.": row.run1,
            "2 з.": row.run2,
            "Итог": "" if row.waiting else (row.total if row.total != "—" else row.run1),
        }
        lines.append("|".join(_cell(values[title], width, align) for title, width, align in columns))
    return Post(f"<pre>{html.escape(chr(10).join(lines))}</pre>")


def render_finisher(state: LiveState) -> Post:
    finisher = state.finisher
    if finisher is None:
        return Post("<pre>Финиш: —</pre>")
    place = f" | место {finisher.place}" if finisher.place else ""
    lines = [
        f"Финиш: №{finisher.start_number} {finisher.full_name}",
        f"Клуб: {finisher.club or '—'}",
        f"{finisher.run_number} заезд: {finisher.value}{place}",
    ]
    return Post(f"<pre>{html.escape(chr(10).join(lines))}</pre>")


def public_url(event: ResultsEvent) -> str:
    base = str(getattr(settings, "SITE_BASE_URL", "")).rstrip("/")
    return f"{base}{reverse('results:public_live', kwargs={'token': event.public_token})}"


def render_link(event: ResultsEvent) -> Post:
    return Post(f'<a href="{html.escape(public_url(event), quote=True)}">Онлайн-результаты: открыть протокол</a>')


# --- Вызовы Bot API (python-telegram-bot асинхронный) ---------------------------------------------


async def _call(method: str, **kwargs):
    async with Bot(token=settings.BOT_TOKEN) as bot:
        return await getattr(bot, method)(**kwargs)


def _send(chat_id: int, post: Post) -> int:
    message = async_to_sync(_call)(
        "send_message", chat_id=chat_id, text=post.text, parse_mode=ParseMode.HTML, disable_web_page_preview=True
    )
    return int(message.message_id)


def _edit(chat_id: int, message_id: int, post: Post) -> int:
    """Возвращает id сообщения (новый, если старое удалили из канала)."""
    try:
        async_to_sync(_call)(
            "edit_message_text",
            chat_id=chat_id,
            message_id=message_id,
            text=post.text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        text = str(exc).lower()
        if "not modified" in text:
            return message_id
        if "not found" in text:
            return _send(chat_id, post)
        raise
    return message_id


def _delete(chat_id: int, message_id: int | None) -> None:
    if not message_id:
        return
    try:
        async_to_sync(_call)("delete_message", chat_id=chat_id, message_id=message_id)
    except TelegramError as exc:
        logger.info("results: не удалось удалить сообщение %s: %s", message_id, exc)


# --- Публикация ------------------------------------------------------------------------------------


def publish(event_id: int) -> bool:
    """Синхронизировать посты канала с текущим состоянием. True — что-то отправлено/отредактировано."""
    event = ResultsEvent.objects.select_related("telegram_channel").get(pk=event_id)
    if not (event.telegram_enabled and event.telegram_channel_id and settings.BOT_TOKEN):
        return False
    pub, _ = TelegramPublication.objects.get_or_create(event=event)
    if pub.published_version == event.version and pub.table_message_id:
        return False
    state = build_state(event)
    if state.focus_group is None:
        return False
    chat_id = event.telegram_channel.chat_id
    table = render_table(state.focus_group, state.focus_run)
    finisher = render_finisher(state)
    link = render_link(event)
    table_key = f"{state.focus_group.key}|{state.focus_run}"
    changed = False
    try:
        if table_key != pub.table_key or not pub.table_message_id:
            _delete(chat_id, pub.finisher_message_id)
            _delete(chat_id, pub.link_message_id)
            pub.table_message_id = _send(chat_id, table)
            pub.finisher_message_id = _send(chat_id, finisher)
            pub.link_message_id = _send(chat_id, link)
            pub.table_key = table_key
            pub.table_hash, pub.finisher_hash, pub.link_hash = table.hash, finisher.hash, link.hash
            changed = True
        else:
            if table.hash != pub.table_hash:
                pub.table_message_id = _edit(chat_id, pub.table_message_id, table)
                pub.table_hash = table.hash
                changed = True
            if finisher.hash != pub.finisher_hash and pub.finisher_message_id:
                pub.finisher_message_id = _edit(chat_id, pub.finisher_message_id, finisher)
                pub.finisher_hash = finisher.hash
                changed = True
            if link.hash != pub.link_hash and pub.link_message_id:
                pub.link_message_id = _edit(chat_id, pub.link_message_id, link)
                pub.link_hash = link.hash
                changed = True
        pub.published_version = event.version
        pub.last_error = ""
    except TelegramError as exc:
        pub.last_error = str(exc)[:1000]
        logger.warning("results: ошибка публикации в Telegram event=%s: %s", event_id, exc)
        raise
    finally:
        pub.save()
    return changed
