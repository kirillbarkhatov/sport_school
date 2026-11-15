"""Применение снимков источника к БД и пересчёт состояния трансляции.

Поток одного тика:  листы → RawSnapshot (если изменились) → parse → diff с БД → запись участников →
пересчёт (автофинализация + фокус) → version++ при любом изменении → публикация (Celery).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from django.db import models, transaction
from django.utils import timezone

from .core import engine
from .core.diff import Change, diff, run_changes
from .core.parsing import ColumnMap, parse_protocol
from .core.protocol import Entry, Group, Protocol
from .core.values import parse_value, validate_manual
from .models import EventLog, RawSnapshot, ResultEntry, ResultGroup, ResultsEvent

logger = logging.getLogger(__name__)


# --- БД ↔ протокол -------------------------------------------------------------------------------


def _entry_from_row(row: ResultEntry, sheet_name: str, group_name: str) -> Entry:
    return Entry(
        sheet_name=sheet_name,
        group_name=group_name,
        start_number=row.start_number,
        full_name=row.full_name,
        club=row.club,
        birth_year=row.birth_year,
        run1=parse_value(row.effective_raw(1)),
        run2=parse_value(row.effective_raw(2)),
        total=parse_value(row.total_raw),
        runs_count=row.runs_count,
        judge_note=row.judge_note,
        sheet_row=row.sheet_row,
    )


def load_protocol(event: ResultsEvent) -> Protocol:
    """Протокол из БД с учётом ручных правок (без автофинализации)."""
    groups = list(event.groups.filter(removed=False).order_by("order_index"))
    by_group: dict[int, list[ResultEntry]] = {g.id: [] for g in groups}
    for row in event.entries.filter(removed=False).order_by("sheet_row", "start_number"):
        by_group.setdefault(row.group_id, []).append(row)
    return Protocol(
        groups=tuple(
            Group(
                sheet_name=g.sheet_name,
                name=g.name,
                order_index=g.order_index,
                entries=tuple(_entry_from_row(r, g.sheet_name, g.name) for r in by_group.get(g.id, [])),
            )
            for g in groups
        ),
        title=event.protocol_title,
        event_date=event.protocol_date,
    )


def column_mappings(event: ResultsEvent) -> dict[str, ColumnMap]:
    result = {}
    for sheet, data in (event.column_mappings or {}).items():
        try:
            result[sheet] = ColumnMap.from_dict(data)
        except TypeError:
            logger.warning("results: некорректная разметка колонок event=%s sheet=%s", event.pk, sheet)
    return result


def effective_protocol(event: ResultsEvent, now: datetime | None = None) -> Protocol:
    """Протокол из БД + виртуальная автофинализация на момент now."""
    protocol = load_protocol(event)
    last_update = {g.key: g.last_result_at for g in event.groups.filter(removed=False) if g.last_result_at}
    rules = engine.FinalizeRules(timeout_sec=event.finalize_timeout_sec, max_missing=event.finalize_max_missing)
    return engine.auto_finalize(protocol, now=now or timezone.now(), last_update_at=last_update, rules=rules)


# --- Тик ------------------------------------------------------------------------------------------


@dataclass
class TickResult:
    stale: bool = False  # снимок отброшен: источник сменился или уже применён более свежий
    changed: bool = False
    changes: list[Change] = field(default_factory=list)
    focus_changed: bool = False
    completed: bool = False


def _log(event: ResultsEvent, kind: str, message: str, payload: dict | None = None, at: datetime | None = None):
    EventLog.objects.create(
        event=event,
        seq=event.next_seq(),
        kind=kind,
        message=message[:500],
        payload=payload or {},
        created_at=at or timezone.now(),
    )


def _describe(change: Change) -> str:
    entry = change.entry
    label = {"run1": "1 заезд", "run2": "2 заезд", "total": "итог", "judge_note": "пометка", "club": "клуб"}
    if change.field == "added":
        return f"Добавлен: {entry.start_number} {entry.full_name} ({entry.group_name})"
    if change.field == "removed":
        return f"Исчез из протокола: {entry.start_number} {entry.full_name}"
    return f"{entry.start_number} {entry.full_name}: {label.get(change.field, change.field)} {change.before or '—'} → {change.after or '—'}"


def _apply_protocol(event: ResultsEvent, protocol: Protocol, changes: list[Change], now: datetime) -> None:
    groups = {g.key: g for g in event.groups.all()}
    seen_groups = set()
    for index, group in enumerate(protocol.groups):
        seen_groups.add(group.key)
        row = groups.get(group.key)
        if row is None:
            row = ResultGroup.objects.create(
                event=event, key=group.key, sheet_name=group.sheet_name, name=group.name, order_index=index
            )
            groups[group.key] = row
        elif row.order_index != index or row.removed:
            row.order_index, row.removed = index, False
            row.save(update_fields=["order_index", "removed"])
    ResultGroup.objects.filter(event=event, removed=False).exclude(key__in=seen_groups).update(removed=True)

    rows = {r.key: r for r in event.entries.all()}
    seen_entries = set()
    touched_groups: set[str] = set()
    changed_runs = {(c.entry.key, c.run_number) for c in changes if c.run_number and c.after}
    for entry in protocol.entries:
        seen_entries.add(entry.key)
        row = rows.get(entry.key) or ResultEntry(event=event, key=entry.key)
        row.group = groups[entry.group_key]
        row.start_number = entry.start_number
        row.full_name = entry.full_name
        row.club = entry.club
        row.birth_year = entry.birth_year
        row.runs_count = entry.runs_count
        row.sheet_row = entry.sheet_row
        row.judge_note = entry.judge_note[:255]
        row.run1_raw, row.run2_raw, row.total_raw = entry.run1.raw[:32], entry.run2.raw[:32], entry.total.raw[:32]
        row.removed = False
        for run in (1, 2):
            if (entry.key, run) in changed_runs:
                setattr(row, f"run{run}_seen_at", now)
                touched_groups.add(entry.group_key)
            elif not entry.run(run).raw:
                setattr(row, f"run{run}_seen_at", None)
        row.save()
    ResultEntry.objects.filter(event=event, removed=False).exclude(key__in=seen_entries).update(removed=True)
    if touched_groups:
        ResultGroup.objects.filter(event=event, key__in=touched_groups).update(last_result_at=now)


def ingest_snapshot(
    event_id: int,
    sheets: dict,
    now: datetime | None = None,
    *,
    source_revision: int | None = None,
    fetched_at: datetime | None = None,
) -> TickResult:
    """Принять снимок листов источника. Безопасно вызывать на каждом опросе: без изменений — только пересчёт.

    source_revision — ревизия источника, с которой начиналось чтение; fetched_at — когда чтение началось.
    Снимок отбрасывается (stale), если источник с тех пор сменили или уже применён более свежий снимок:
    так запоздавшее чтение старого XLSX не перезапишет результаты нового.
    """
    now = now or timezone.now()
    fetched_at = fetched_at or now
    with transaction.atomic():
        event = ResultsEvent.objects.select_for_update().get(pk=event_id)
        if source_revision is not None and source_revision != event.source_revision:
            return TickResult(stale=True)
        if event.snapshot_fetched_at and fetched_at < event.snapshot_fetched_at:
            return TickResult(stale=True)
        previous_poll = event.last_polled_at
        event.last_polled_at = now
        candidates: list[engine.FocusTarget] = []
        content_hash = RawSnapshot.hash_sheets(sheets)
        last = event.snapshots.order_by("-taken_at", "-id").values("content_hash", "source_revision").first()
        result = TickResult()
        source_changed = last is None or last["source_revision"] != event.source_revision
        if source_changed or content_hash != last["content_hash"]:
            RawSnapshot.objects.create(
                event=event,
                taken_at=now,
                content_hash=content_hash,
                source_revision=event.source_revision,
                data=RawSnapshot.pack(sheets),
            )
            event.snapshot_fetched_at = fetched_at
            protocol = parse_protocol(sheets, column_mappings(event))
            previous = load_protocol(event)
            first_load = not previous.groups
            changes = diff(previous if not first_load else None, protocol)
            if changes:
                _apply_protocol(event, protocol, changes, now)
                result.changed = True
                result.changes = changes
                if not first_load:
                    candidates = engine.focus_candidates(run_changes(changes))
                if first_load:
                    _log(event, EventLog.Kind.ENTRY, f"Протокол загружен: {len(protocol.entries)} участников", at=now)
                else:
                    for change in changes:
                        kind = (
                            EventLog.Kind.RESULT
                            if change.run_number or change.field == "total"
                            else EventLog.Kind.ENTRY
                        )
                        _log(event, kind, _describe(change), {"key": change.entry.key, "field": change.field}, at=now)
            event.protocol_title = protocol.title[:255]
            event.protocol_date = protocol.event_date[:32]
            event.parse_warnings = list(protocol.warnings)
        if not result.changed and previous_poll is not None:
            result.changed = _finalization_changed(event, previous_poll, now)
        _recompute(event, now, result, candidates=candidates)
        event.save()
    return result


def _finalization_changed(event: ResultsEvent, before: datetime, now: datetime) -> bool:
    """Изменилась ли автофинализация между двумя моментами (таймаут истёк без новых данных)."""
    return effective_protocol(event, before) != effective_protocol(event, now)


def _recompute(event: ResultsEvent, now: datetime, result: TickResult, candidates) -> None:
    protocol = effective_protocol(event, now)
    previous_focus = engine.Focus.from_dict(event.focus)
    focus = engine.next_focus(protocol, previous_focus, candidates)
    if focus != previous_focus:
        event.focus = focus.to_dict()
        result.focus_changed = True
        if focus.current != previous_focus.current and focus.current is not None:
            group = protocol.group(focus.current.group_key)
            name = group.name if group else focus.current.group_key
            _log(event, EventLog.Kind.FOCUS, f"Сейчас: {name}, {focus.current.run_number} заезд", focus.to_dict(), now)
    result.completed = bool(protocol.groups) and all(g.completed() for g in protocol.groups)
    if result.changed or result.focus_changed:
        event.version += 1
        event.last_changed_at = now


def recompute(event_id: int, now: datetime | None = None) -> TickResult:
    """Пересчёт без нового снимка (автофинализация по таймауту, ручные правки)."""
    now = now or timezone.now()
    with transaction.atomic():
        event = ResultsEvent.objects.select_for_update().get(pk=event_id)
        result = TickResult()
        if event.last_polled_at is not None:
            result.changed = _finalization_changed(event, event.last_polled_at, now)
        event.last_polled_at = now
        _recompute(event, now, result, candidates=[])
        event.save()
    return result


# --- Ручные правки и жизненный цикл ---------------------------------------------------------------


def set_manual_result(entry_id: int, run_number: int, value: str | None, user=None) -> ResultEntry:
    """value=None — снять правку (вернуть значение источника). Пустая строка — явно очистить.

    Некорректное значение (не время и не DNS/DNF/DSQ) — InvalidResult, ничего не сохраняется."""
    if run_number not in (1, 2):
        raise ValueError("run_number must be 1 or 2")
    normalized = None if value is None else validate_manual(value)
    now = timezone.now()
    with transaction.atomic():
        entry = ResultEntry.objects.select_related("event").get(pk=entry_id)
        event = ResultsEvent.objects.select_for_update().get(pk=entry.event_id)
        before = entry.effective_raw(run_number)
        setattr(entry, f"run{run_number}_manual", normalized)
        if normalized:
            setattr(entry, f"run{run_number}_seen_at", now)
        entry.save()
        ResultGroup.objects.filter(pk=entry.group_id).update(last_result_at=now)
        who = getattr(user, "email", "") or "—"
        after = entry.effective_raw(run_number)
        _log(
            event,
            EventLog.Kind.MANUAL,
            f"{entry.start_number} {entry.full_name}: {run_number} заезд {before or '—'} → {after or '—'} ({who})",
            {"entry_id": entry.pk, "run": run_number, "manual": normalized},
            now,
        )
        candidates = []
        if normalized and parse_value(normalized).is_time:
            group = entry.group
            candidates = [engine.FocusTarget(group.sheet_name, group.key, run_number)]
        result = TickResult(changed=True)
        _recompute(event, now, result, candidates)
        event.save()
    return entry


def set_status(event_id: int, status: str, user=None) -> ResultsEvent:
    now = timezone.now()
    with transaction.atomic():
        event = ResultsEvent.objects.select_for_update().get(pk=event_id)
        if event.status == status:
            return event
        event.status = status
        if status == ResultsEvent.Status.RUNNING and not event.started_at:
            event.started_at = now
        if status == ResultsEvent.Status.FINISHED:
            event.finished_at = now
        who = getattr(user, "email", "") or "система"
        _log(event, EventLog.Kind.LIFECYCLE, f"{event.get_status_display()} ({who})", {"status": status}, now)
        event.version += 1
        event.save()
    return event


def log_message(event_id: int, message: str, user=None) -> None:
    who = getattr(user, "email", "") or "система"
    with transaction.atomic():
        event = ResultsEvent.objects.select_for_update().get(pk=event_id)
        _log(event, EventLog.Kind.LIFECYCLE, f"{message} ({who})")
        event.version += 1
        event.save(update_fields=["seq", "version", "updated_at"])


def record_error(event_id: int, message: str) -> None:
    with transaction.atomic():
        event = ResultsEvent.objects.select_for_update().get(pk=event_id)
        if event.last_error != message:
            _log(event, EventLog.Kind.ERROR, message)
        event.last_error = message
        event.save(update_fields=["last_error", "seq", "updated_at"])


def clear_error(event_id: int) -> None:
    ResultsEvent.objects.filter(pk=event_id).exclude(last_error="").update(last_error="")


# --- Ручной состав (источник «Ручной ввод») -------------------------------------------------------

MANUAL_SHEET = "Протокол"


class RosterError(ValueError):
    """Ошибка подготовки состава (показывается пользователю)."""


def parse_roster(text: str, runs_count: int = 2) -> list[dict]:
    """Список участников: строка без «;» — название группы, далее строки «номер; Фамилия Имя; клуб; год»."""
    rows, group = [], ""
    for number, line in enumerate((text or "").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in line.replace("\t", ";").split(";")]
        if len(parts) == 1:
            group = parts[0]
            continue
        if not group:
            raise RosterError(f"Строка {number}: сначала укажите группу отдельной строкой")
        if not parts[0].isdigit() or len(parts) < 2 or not parts[1]:
            raise RosterError(f"Строка {number}: ожидается «номер; Фамилия Имя; клуб; год рождения»")
        year = parts[3] if len(parts) > 3 else ""
        if year and not (year.isdigit() and len(year) == 4):
            raise RosterError(f"Строка {number}: год рождения — четыре цифры")
        rows.append(
            {
                "group_name": group,
                "start_number": int(parts[0]),
                "full_name": " ".join(parts[1].split()),
                "club": parts[2] if len(parts) > 2 else "",
                "birth_year": int(year) if year else None,
                "runs_count": runs_count,
            }
        )
    if not rows:
        raise RosterError("Список пуст: добавьте группу и хотя бы одного участника")
    return rows


def add_manual_entries(event_id: int, rows: list[dict], user=None) -> int:
    """Добавить участников ручного эфира. Стартовые номера в эфире уникальны; всё или ничего."""
    from .core.protocol import entry_key, group_key

    now = timezone.now()
    with transaction.atomic():
        event = ResultsEvent.objects.select_for_update().get(pk=event_id)
        if event.source_kind != ResultsEvent.SourceKind.MANUAL:
            raise RosterError("Состав вручную добавляется только при источнике «Ручной ввод»")
        taken = set(event.entries.filter(removed=False).values_list("start_number", flat=True))
        seen = set()
        for row in rows:
            if row["start_number"] in taken or row["start_number"] in seen:
                raise RosterError(f"Стартовый номер {row['start_number']} уже занят")
            if row["start_number"] < 1 or not row["full_name"]:
                raise RosterError("Нужны стартовый номер и фамилия с именем")
            seen.add(row["start_number"])
        groups = {g.name: g for g in event.groups.filter(removed=False)}
        order = max((g.order_index for g in groups.values()), default=-1)
        for row in rows:
            group = groups.get(row["group_name"])
            if group is None:
                order += 1
                group, _ = ResultGroup.objects.update_or_create(
                    event=event,
                    key=group_key(MANUAL_SHEET, row["group_name"]),
                    defaults={
                        "sheet_name": MANUAL_SHEET,
                        "name": row["group_name"],
                        "order_index": order,
                        "removed": False,
                    },
                )
                groups[group.name] = group
            row_index = (event.entries.filter(group=group).aggregate(m=models.Max("sheet_row"))["m"] or 0) + 1
            ResultEntry.objects.update_or_create(
                event=event,
                key=entry_key(group.sheet_name, group.name, row["start_number"], row["full_name"]),
                defaults={
                    "group": group,
                    "start_number": row["start_number"],
                    "full_name": row["full_name"][:255],
                    "club": (row.get("club") or "")[:255],
                    "birth_year": row.get("birth_year"),
                    "runs_count": row.get("runs_count") or 2,
                    "sheet_row": row_index,
                    "removed": False,
                },
            )
        who = getattr(user, "email", "") or "—"
        _log(event, EventLog.Kind.ENTRY, f"Добавлено участников вручную: {len(rows)} ({who})", at=now)
        event.version += 1
        event.last_changed_at = now
        event.save()
    return len(rows)


def remove_manual_entry(entry_id: int, user=None) -> None:
    with transaction.atomic():
        entry = ResultEntry.objects.select_related("event").get(pk=entry_id)
        event = ResultsEvent.objects.select_for_update().get(pk=entry.event_id)
        if event.source_kind != ResultsEvent.SourceKind.MANUAL:
            raise RosterError("Убрать участника можно только в ручном эфире — в остальных правьте протокол")
        entry.removed = True
        entry.save(update_fields=["removed"])
        if not event.entries.filter(group=entry.group, removed=False).exists():
            ResultGroup.objects.filter(pk=entry.group_id).update(removed=True)
        who = getattr(user, "email", "") or "—"
        _log(event, EventLog.Kind.ENTRY, f"Убран участник: {entry.start_number} {entry.full_name} ({who})")
        event.version += 1
        event.save()
