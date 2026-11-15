"""Неизменяемая модель протокола соревнования.

Лист таблицы — дисциплина/трасса: на нём группы идут по очереди в 1-м заезде, затем (если заездов два)
все группы в том же порядке проходят 2-й заезд.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from .values import EMPTY, RunValue


def group_key(sheet_name: str, group_name: str) -> str:
    return f"{sheet_name}|{group_name}"


def entry_key(sheet_name: str, group_name: str, start_number: int, full_name: str) -> str:
    return f"{sheet_name}|{group_name}|{start_number}|{full_name}"


@dataclass(frozen=True, slots=True)
class Entry:
    sheet_name: str
    group_name: str
    start_number: int
    full_name: str
    club: str = ""
    birth_year: int | None = None
    run1: RunValue = EMPTY
    run2: RunValue = EMPTY
    total: RunValue = EMPTY
    runs_count: int = 2
    judge_note: str = ""
    sheet_row: int = 0
    auto_finalized: bool = False

    @property
    def key(self) -> str:
        return entry_key(self.sheet_name, self.group_name, self.start_number, self.full_name)

    @property
    def group_key(self) -> str:
        return group_key(self.sheet_name, self.group_name)

    def run(self, number: int) -> RunValue:
        return self.run1 if number == 1 else self.run2

    def has_progress(self) -> bool:
        return not (self.run1.is_empty and self.run2.is_empty and self.total.is_empty)

    def has_run2(self) -> bool:
        return self.runs_count > 1 and not self.run2.is_empty

    def eligible_for_run2(self) -> bool:
        """Статус в 1-м заезде (DNS/DNF/DSQ…) — во 2-й не стартует."""
        return self.runs_count > 1 and not self.run1.is_status

    def effective_total(self) -> RunValue:
        """Итог считается из заездов, чтобы ручная правка заезда сразу меняла итог и место.

        Порядок: статус любого заезда → один заезд: его время → два времени: сумма → колонка «Результат»
        протокола (только если заездов не хватает для расчёта, например итог внесён без раскладки).
        """
        for value in (self.run2, self.run1):
            if value.is_status:
                return value
        if self.runs_count <= 1 and self.run1.is_time:
            return self.run1
        if self.run1.is_time and self.run2.is_time:
            return RunValue.time(self.run1.ms + self.run2.ms)
        if self.total.is_status:
            return self.total
        if self.total.is_time and (self.runs_count <= 1 or not self.run2.is_empty or self.run1.is_empty):
            return self.total
        return EMPTY

    def ranking_value(self) -> RunValue:
        """По чему ставится место сейчас: итог после 2-го заезда, иначе 1-й заезд."""
        if self.runs_count <= 1 or self.has_run2():
            return self.effective_total()
        return self.run1

    def is_finished(self) -> bool:
        if self.effective_total().is_status:
            return True
        if self.runs_count <= 1:
            return not self.run1.is_empty or self.total.is_time
        return self.has_run2()

    def last_run_value(self) -> RunValue:
        return self.run2 if not self.run2.is_empty else self.run1

    def with_runs(self, **changes) -> Entry:
        return replace(self, **changes)


@dataclass(frozen=True, slots=True)
class Group:
    sheet_name: str
    name: str
    entries: tuple[Entry, ...] = ()
    order_index: int = 0

    @property
    def key(self) -> str:
        return group_key(self.sheet_name, self.name)

    @property
    def runs_count(self) -> int:
        return max((entry.runs_count for entry in self.entries), default=2)

    @property
    def is_single_run(self) -> bool:
        return bool(self.entries) and all(entry.runs_count <= 1 for entry in self.entries)

    def started(self) -> bool:
        return any(entry.has_progress() for entry in self.entries)

    def completed(self) -> bool:
        return self.started() and all(entry.is_finished() for entry in self.entries)

    def run_started(self, number: int) -> bool:
        return any(not entry.run(number).is_empty for entry in self.entries)

    def run_closed(self, number: int) -> bool:
        """Все, кто должен был пройти заезд, прошли его (время или статус)."""
        if not self.entries:
            return False
        if number == 1:
            return all(not entry.run1.is_empty or entry.effective_total().is_status for entry in self.entries)
        if self.is_single_run:
            return self.run_closed(1)
        return all(
            not entry.run2.is_empty or not entry.eligible_for_run2() or entry.effective_total().is_status
            for entry in self.entries
        )

    def with_entries(self, entries: tuple[Entry, ...]) -> Group:
        return replace(self, entries=entries)


@dataclass(frozen=True, slots=True)
class Protocol:
    groups: tuple[Group, ...] = ()
    title: str = ""
    event_date: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def entries(self) -> tuple[Entry, ...]:
        return tuple(entry for group in self.groups for entry in group.entries)

    @property
    def sheets(self) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for group in self.groups:
            seen.setdefault(group.sheet_name, None)
        return tuple(seen)

    def group(self, key: str) -> Group | None:
        return next((group for group in self.groups if group.key == key), None)

    def sheet_groups(self, sheet_name: str) -> tuple[Group, ...]:
        return tuple(group for group in self.groups if group.sheet_name == sheet_name)

    def has_progress(self) -> bool:
        return any(group.started() for group in self.groups)

    def competition_format(self) -> str:
        return "single_run" if self.groups and all(group.is_single_run for group in self.groups) else "two_run"
