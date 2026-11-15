"""Движок трансляции: места, фазы, автофинализация, фокус, прогнозы.

Все функции чистые: состояние и текущее время передаются явно, скрытого состояния нет.
Это единственное место, где определяется, «закрыт ли заезд» и «какая группа сейчас едет».
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from statistics import median

from .protocol import Entry, Group, Protocol
from .values import STATUS_ORDER, RunValue

AUTO_DNS = RunValue.of_status("DNS", raw="DNS")
AUTO_NOTE = "автофинализация: DNS"


class Phase(StrEnum):
    NOT_STARTED = "not_started"
    RUN1 = "run1"
    BREAK = "break_after_run1"
    RUN2 = "run2"
    COMPLETED = "completed"


# --- Места ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Standing:
    place: int
    entry: Entry
    value: RunValue  # по чему поставлено место (1-й заезд или итог)
    gap_ms: int | None  # отставание от лидера
    waiting: bool  # ещё не прошёл текущий заезд


def _waiting(entry: Entry, second_run_phase: bool) -> bool:
    if entry.ranking_value().is_status:
        return False
    if second_run_phase and entry.runs_count > 1:
        return entry.run2.is_empty and entry.eligible_for_run2()
    return entry.run1.is_empty


def _sort_key(entry: Entry, second_run_phase: bool) -> tuple:
    """Время → пометка судьи → статус → ещё не стартовал (в стартовом порядке)."""
    value = entry.ranking_value()
    waiting = _waiting(entry, second_run_phase)
    if waiting:
        return (3, 0, entry.sheet_row, entry.start_number)
    if value.is_time and not entry.judge_note:
        return (0, value.ms, entry.start_number, entry.sheet_row)
    if value.is_time:
        return (1, value.ms, entry.start_number, entry.sheet_row)
    return (2, STATUS_ORDER.get(value.status, 9), entry.start_number, entry.sheet_row)


def standings(group: Group) -> list[Standing]:
    second_run_phase = any(entry.has_run2() for entry in group.entries)
    ranked = sorted(group.entries, key=lambda e: _sort_key(e, second_run_phase))
    leader = next(
        (e.ranking_value().ms for e in ranked if e.ranking_value().is_time and not _waiting(e, second_run_phase)),
        None,
    )
    result = []
    for place, entry in enumerate(ranked, start=1):
        value = entry.ranking_value()
        waiting = _waiting(entry, second_run_phase)
        gap = value.ms - leader if (leader is not None and value.is_time and not waiting) else None
        result.append(Standing(place=place, entry=entry, value=value, gap_ms=gap, waiting=waiting))
    return result


def run1_standings(group: Group) -> list[Standing]:
    """Места по итогам 1-го заезда (для таблицы «после 1-го заезда» во время 2-го)."""
    return standings(group.with_entries(tuple(replace(e, run2=RunValue(), total=RunValue()) for e in group.entries)))


# --- Фазы ----------------------------------------------------------------------------------------


def group_phase(group: Group) -> Phase:
    return _phase(group.entries)


def sheet_phase(protocol: Protocol, sheet_name: str) -> Phase:
    return _phase([e for g in protocol.sheet_groups(sheet_name) for e in g.entries])


def _phase(entries: list[Entry] | tuple[Entry, ...]) -> Phase:
    if not entries:
        return Phase.NOT_STARTED
    if all(e.is_finished() for e in entries) and any(e.has_progress() for e in entries):
        return Phase.COMPLETED
    if any(not e.run2.is_empty for e in entries):
        return Phase.RUN2
    run1_started = any(not e.run1.is_empty for e in entries)
    if run1_started and all(not e.run1.is_empty for e in entries):
        return Phase.BREAK
    if run1_started:
        return Phase.RUN1
    return Phase.NOT_STARTED


def current_group(protocol: Protocol) -> Group | None:
    return next((g for g in protocol.groups if g.started() and not g.completed()), None)


# --- Автофинализация -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FinalizeRules:
    timeout_sec: int = 300  # сколько ждать после последнего результата в группе
    max_missing: int = 2  # закрывать, только если не хватает не больше N участников


DEFAULT_RULES = FinalizeRules()


def _missing(group: Group, run_number: int) -> list[Entry]:
    if run_number == 1:
        return [e for e in group.entries if e.run1.is_empty and not e.effective_total().is_status]
    return [
        e
        for e in group.entries
        if not e.run1.is_empty and e.eligible_for_run2() and e.run2.is_empty and not e.effective_total().is_status
    ]


def auto_finalize(
    protocol: Protocol,
    *,
    now: datetime,
    last_update_at: dict[str, datetime],
    rules: FinalizeRules = DEFAULT_RULES,
) -> Protocol:
    """Не прошедшим заезд ставится виртуальный DNS, если группа «явно закончилась»:

    - не хватает ≤ max_missing участников, и на этом же листе уже стартовала следующая группа в этом заезде; или
    - не хватает ≤ max_missing участников, и с последнего результата прошло ≥ timeout_sec.

    Значения в источнике не меняются — финализация пересчитывается на каждом тике.
    """
    groups = list(protocol.groups)
    result = []
    for index, group in enumerate(groups):
        phase = group_phase(group)
        if phase not in (Phase.RUN1, Phase.RUN2) or group.completed():
            result.append(group)
            continue
        run_number = 2 if phase is Phase.RUN2 and not group.is_single_run else 1
        missing = _missing(group, run_number)
        if not missing or len(missing) > rules.max_missing:
            result.append(group)
            continue
        next_started = any(
            later.sheet_name == group.sheet_name and later.run_started(run_number) for later in groups[index + 1 :]
        )
        last = last_update_at.get(group.key)
        timed_out = last is not None and (now - last) >= timedelta(seconds=rules.timeout_sec)
        if not (next_started or timed_out):
            result.append(group)
            continue
        missing_keys = {e.key for e in missing}
        result.append(
            group.with_entries(
                tuple(
                    replace(
                        e,
                        auto_finalized=True,
                        judge_note=f"{e.judge_note}; {AUTO_NOTE}" if e.judge_note else AUTO_NOTE,
                        **{"run1" if run_number == 1 else "run2": AUTO_DNS},
                    )
                    if e.key in missing_keys
                    else e
                    for e in group.entries
                )
            )
        )
    return replace(protocol, groups=tuple(result))


# --- Фокус ---------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FocusTarget:
    sheet_name: str
    group_key: str
    run_number: int


@dataclass(frozen=True, slots=True)
class Focus:
    """Что показываем как «сейчас едет». deferred — кандидат с другого листа/заезда, ждущий закрытия текущего."""

    current: FocusTarget | None = None
    deferred: FocusTarget | None = None

    def to_dict(self) -> dict:
        def pack(target: FocusTarget | None) -> dict | None:
            if target is None:
                return None
            return {"sheet_name": target.sheet_name, "group_key": target.group_key, "run_number": target.run_number}

        return {"current": pack(self.current), "deferred": pack(self.deferred)}

    @classmethod
    def from_dict(cls, data: dict | None) -> Focus:
        data = data or {}

        def unpack(raw: dict | None) -> FocusTarget | None:
            if not raw or not raw.get("group_key") or raw.get("run_number") not in (1, 2):
                return None
            return FocusTarget(raw.get("sheet_name", ""), raw["group_key"], int(raw["run_number"]))

        return cls(current=unpack(data.get("current")), deferred=unpack(data.get("deferred")))


def _target_closed(protocol: Protocol, target: FocusTarget) -> bool:
    """Однозаездная группа закрывается сама; иначе заезд закрыт, когда его прошли все группы листа."""
    group = protocol.group(target.group_key)
    if group is not None and group.is_single_run:
        return group.run_closed(1)
    sheet_groups = protocol.sheet_groups(target.sheet_name)
    return bool(sheet_groups) and all(g.run_closed(target.run_number) for g in sheet_groups)


def focus_candidates(changes: list[tuple[Entry, int]]) -> list[FocusTarget]:
    """Новые времена (не статусы) двигают фокус. changes — (участник, номер заезда, в котором появилось время)."""
    return [FocusTarget(e.sheet_name, e.group_key, run) for e, run in changes if e.run(run).is_time]


def next_focus(protocol: Protocol, previous: Focus, candidates: list[FocusTarget]) -> Focus:
    current, deferred = previous.current, previous.deferred
    if current is not None and protocol.group(current.group_key) is None:
        current = None  # группа исчезла из протокола (переименовали лист/группу)

    for candidate in candidates:
        if current is None:
            current = candidate
        elif candidate.sheet_name == current.sheet_name and candidate.run_number == current.run_number:
            current = candidate  # следующая группа того же заезда на том же листе
        elif candidate != current:
            deferred = candidate

    if current is None and deferred is None:
        group = current_group(protocol)
        if group is not None:
            run = 2 if group.run_started(2) and not group.is_single_run else 1
            current = FocusTarget(group.sheet_name, group.key, run)

    if deferred is not None and (current is None or _target_closed(protocol, current)):
        current, deferred = deferred, None
    if deferred is not None and deferred == current:
        deferred = None
    return Focus(current=current, deferred=deferred)


# --- Прогноз времени старта ----------------------------------------------------------------------


def estimate_starts(
    group_entries: list[Entry],
    *,
    run_number: int,
    seen_at: dict[tuple[str, int], datetime],
    now: datetime,
) -> dict[str, datetime]:
    """Ориентировочное время финиша ожидающих участников листа в текущем заезде.

    Базовый темп — половина первого времени заезда (на трассе обычно двое); по мере поступления результатов
    он уточняется медианой реальных интервалов между финишами (с отсечкой пауз оператора).
    """
    queue = [e for e in group_entries if run_number == 1 or e.eligible_for_run2()]
    done = [e for e in queue if e.run(run_number).is_time]
    waiting = [e for e in queue if e.run(run_number).is_empty]
    if not done or not waiting:
        return {}
    times = [e.run(run_number).ms for e in done]
    base = times[0] / 2 / 1000
    tempo = base
    stamps = sorted(ts for e in done if (ts := seen_at.get((e.key, run_number))) is not None)
    intervals = [
        delta
        for a, b in zip(stamps, stamps[1:], strict=False)
        if 0.1 < (delta := (b - a).total_seconds()) <= base * 2.5
    ]
    if len(stamps) >= 3 and intervals:
        observed = min(max(median(intervals), base * 0.55), base * 1.6)
        weight = min(len(intervals), 6) / 6
        tempo = base * (1 - weight) + observed * weight
    return {e.key: now + timedelta(seconds=tempo * index) for index, e in enumerate(waiting, start=1)}


# --- Аналитика -----------------------------------------------------------------------------------


def _win_probability(gap_sec: float, sigma: float) -> float:
    return 0.5 * math.erfc(gap_sec / (2.0 * max(sigma, 0.10)))


def _run2_sigma(times_sec: list[float]) -> float:
    if len(times_sec) < 4:
        return 0.80
    center = median(times_sec)
    mad = median(abs(t - center) for t in times_sec)
    return min(max(1.4826 * mad, 0.35), 1.80)


@dataclass(frozen=True, slots=True)
class Outlook:
    """Прогноз после 1-го заезда: вероятное итоговое место и шансы обогнать соседа / быть обогнанным."""

    likely_place: int
    p_up: float
    p_down: float
    label: str


def run1_outlook(group: Group) -> dict[str, Outlook]:
    if group.is_single_run or not group.run_closed(1):
        return {}
    ranked = sorted((e for e in group.entries if e.run1.is_time), key=lambda e: (e.run1.ms, e.start_number))
    if len(ranked) < 3:
        return {}
    seconds = [e.run1.ms / 1000 for e in ranked]
    sigma = _run2_sigma(seconds)
    result = {}
    for index, entry in enumerate(ranked):
        own = seconds[index]
        gain = sum(_win_probability(own - other, sigma) for other in seconds[max(0, index - 5) : index])
        loss = sum(_win_probability(other - own, sigma) for other in seconds[index + 1 : index + 6])
        place = index + 1
        likely = max(1, min(len(ranked), round(place - (gain - loss))))
        p_up = _win_probability(own - seconds[index - 1], sigma) if index > 0 else 0.0
        p_down = _win_probability(seconds[index + 1] - own, sigma) if index + 1 < len(ranked) else 0.0
        if place == 1:
            label = "Лидер под давлением" if p_down >= 0.42 else "Лидер с запасом"
        elif place <= 3:
            label = "Подиум: риск потери" if p_down >= 0.42 else "Подиум: стабильно"
        elif likely < place:
            label = "Шанс отыграть места"
        elif p_down >= 0.42:
            label = "Риск потерять места"
        else:
            label = "Стабильная позиция"
        result[entry.key] = Outlook(likely_place=likely, p_up=p_up, p_down=p_down, label=label)
    return result


def place_changes(group: Group) -> dict[str, int]:
    """Сколько мест отыграл (+) / потерял (−) участник во 2-м заезде относительно 1-го."""
    if group.is_single_run:
        return {}
    before = {s.entry.key: s.place for s in run1_standings(group) if not s.waiting}
    after = {s.entry.key: s.place for s in standings(group) if not s.waiting and s.entry.has_run2()}
    return {key: before[key] - place for key, place in after.items() if key in before}


def best_run_keys(group: Group, run_number: int) -> set[str]:
    timed = [e for e in group.entries if e.run(run_number).is_time]
    if not timed:
        return set()
    best = min(e.run(run_number).ms for e in timed)
    return {e.key for e in timed if e.run(run_number).ms == best}


@dataclass(frozen=True, slots=True)
class ClubStats:
    club: str
    participants: int
    finishers: int
    podiums: int
    top10: int


def club_stats(groups: tuple[Group, ...] | list[Group]) -> list[ClubStats]:
    acc: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    labels: dict[str, str] = {}
    for group in groups:
        for standing in standings(group):
            club = standing.entry.club or "—"
            key = club.lower().replace("ё", "е")
            labels.setdefault(key, club)
            row = acc[key]
            row[0] += 1
            if standing.entry.is_finished() and standing.value.is_time:
                row[1] += 1
                row[2] += standing.place <= 3
                row[3] += standing.place <= 10
    stats = [ClubStats(labels[k], *v) for k, v in acc.items()]
    return sorted(stats, key=lambda s: (-s.podiums, -s.top10, -s.finishers, s.club.lower()))
