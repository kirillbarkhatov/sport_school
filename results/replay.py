"""Офлайн-воспроизведение трансляции по сырым снимкам — без БД и сети.

Позволяет взять снимки реального старта (RawSnapshot), прогнать их через текущую версию движка
и увидеть, как менялись фокус, финализация и места. Найденную ошибку легко превратить в тест.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime

from .core import engine
from .core.diff import diff, run_changes
from .core.parsing import ColumnMap, parse_protocol
from .core.protocol import Protocol


@dataclass(frozen=True)
class ReplayStep:
    at: datetime
    protocol: Protocol  # с автофинализацией
    focus: engine.Focus
    changes: int
    events: tuple[str, ...]


def replay(
    snapshots: Iterable[tuple[datetime, dict]],
    *,
    mappings: dict[str, ColumnMap] | None = None,
    rules: engine.FinalizeRules = engine.DEFAULT_RULES,
) -> Iterator[ReplayStep]:
    previous: Protocol | None = None
    focus = engine.Focus()
    last_update: dict[str, datetime] = {}
    completed: set[str] = set()
    for at, sheets in snapshots:
        protocol = parse_protocol(sheets, mappings)
        changes = diff(previous, protocol)
        runs = run_changes(changes) if previous is not None else []
        for entry, _ in runs:
            last_update[entry.group_key] = at
        effective = engine.auto_finalize(protocol, now=at, last_update_at=last_update, rules=rules)
        new_focus = engine.next_focus(effective, focus, engine.focus_candidates(runs))
        events = []
        if new_focus.current != focus.current and new_focus.current:
            group = effective.group(new_focus.current.group_key)
            events.append(f"фокус → {group.name if group else '?'} / {new_focus.current.run_number} заезд")
        for group in effective.groups:
            if group.completed() and group.key not in completed:
                completed.add(group.key)
                auto = sum(e.auto_finalized for e in group.entries)
                events.append(f"группа завершена: {group.name}" + (f" (автофинализация: {auto})" if auto else ""))
        focus = new_focus
        previous = protocol
        yield ReplayStep(at=at, protocol=effective, focus=focus, changes=len(changes), events=tuple(events))
