"""Изменения между двумя снимками протокола."""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import Entry, Protocol
from .values import RunValue

TRACKED = ("run1", "run2", "total", "judge_note", "club")


@dataclass(frozen=True, slots=True)
class Change:
    entry: Entry
    field: str  # run1 | run2 | total | judge_note | club | added | removed
    before: str
    after: str

    @property
    def run_number(self) -> int | None:
        return {"run1": 1, "run2": 2}.get(self.field)


def _raw(value: RunValue | str) -> str:
    return value.raw if isinstance(value, RunValue) else (value or "")


def diff(previous: Protocol | None, current: Protocol) -> list[Change]:
    old = {e.key: e for e in previous.entries} if previous else {}
    new = {e.key: e for e in current.entries}
    changes: list[Change] = []
    for key, entry in new.items():
        before = old.get(key)
        if before is None:
            changes.append(Change(entry, "added", "", ""))
            for name in ("run1", "run2", "total"):
                if raw := _raw(getattr(entry, name)):
                    changes.append(Change(entry, name, "", raw))
            continue
        for name in TRACKED:
            a, b = _raw(getattr(before, name)), _raw(getattr(entry, name))
            if a != b:
                changes.append(Change(entry, name, a, b))
    for key, entry in old.items():
        if key not in new:
            changes.append(Change(entry, "removed", "", ""))
    return changes


def run_changes(changes: list[Change]) -> list[tuple[Entry, int]]:
    """(участник, заезд) для изменений времени/статуса в заездах — вход для фокуса и прогноза темпа."""
    return [(c.entry, c.run_number) for c in changes if c.run_number and c.after]
