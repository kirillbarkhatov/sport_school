"""Модель отображения трансляции — общая для live-страницы и Telegram."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from django.utils import timezone

from .core import engine
from .core.protocol import Group, Protocol
from .core.values import format_gap
from .models import ResultsEvent
from .services import effective_protocol

PHASE_LABELS = {
    engine.Phase.NOT_STARTED: "Не началась",
    engine.Phase.RUN1: "1 заезд",
    engine.Phase.BREAK: "1 заезд закрыт · перерыв",
    engine.Phase.RUN2: "2 заезд",
    engine.Phase.COMPLETED: "Завершена",
}


@dataclass
class Row:
    place: int
    start_number: int
    full_name: str
    club: str
    birth_year: int | None
    run1: str
    run2: str
    total: str
    gap: str
    note: str
    waiting: bool
    auto_finalized: bool
    highlight: bool
    delta: int | None = None
    best_run1: bool = False
    best_run2: bool = False
    outlook: str = ""
    eta: str = ""
    rank_note: str = ""  # почему место ниже, чем по времени (пометка судьи / статус)


@dataclass
class GroupView:
    key: str
    sheet_name: str
    name: str
    phase: str
    phase_label: str
    is_single_run: bool
    rows: list[Row]
    run1_rows: list[Row] = field(default_factory=list)  # места после 1-го заезда (во время 2-го)
    finishers: int = 0  # показали итоговое время
    out: int = 0  # итог — статус (DNS/DNF/DSQ…)
    waiting: int = 0  # исход ещё не зафиксирован
    total: int = 0

    @property
    def decided(self) -> int:
        return self.finishers + self.out


@dataclass
class Finisher:
    start_number: int
    full_name: str
    club: str
    run_number: int
    value: str
    place: int | None


@dataclass
class LiveState:
    event: ResultsEvent
    title: str
    date: str
    status_label: str
    focus_group: GroupView | None
    focus_run: int
    groups: list[GroupView]
    finisher: Finisher | None
    club_stats: list[engine.ClubStats]
    completed: bool
    finished: bool  # трансляция завершена или все группы закончили — показываем итоги
    started: bool  # есть хотя бы один результат
    generated_at: datetime


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def _rows(
    group: Group,
    standings: list[engine.Standing],
    *,
    highlight: str,
    etas: dict[str, datetime] | None = None,
    with_analytics: bool = True,
) -> list[Row]:
    deltas = engine.place_changes(group) if with_analytics else {}
    best1 = engine.best_run_keys(group, 1) if with_analytics else set()
    best2 = engine.best_run_keys(group, 2) if with_analytics else set()
    outlook = engine.run1_outlook(group) if with_analytics and engine.group_phase(group) is engine.Phase.BREAK else {}
    rows = []
    for s in standings:
        e = s.entry
        eta = (etas or {}).get(e.key)
        rows.append(
            Row(
                place=s.place,
                start_number=e.start_number,
                full_name=e.full_name,
                club=e.club,
                birth_year=e.birth_year,
                run1=e.run1.display() or "—",
                run2="" if group.is_single_run else (e.run2.display() or "—"),
                total=e.effective_total().display() or "—",
                gap=format_gap(s.gap_ms),
                note=e.judge_note,
                waiting=s.waiting,
                auto_finalized=e.auto_finalized,
                highlight=bool(highlight) and highlight in _norm(e.club),
                delta=deltas.get(e.key),
                best_run1=e.key in best1,
                best_run2=e.key in best2,
                outlook=outlook[e.key].label if e.key in outlook else "",
                rank_note=_rank_note(s),
                eta=timezone.localtime(eta).strftime("%H:%M") if eta else "",
            )
        )
    return rows


def _rank_note(standing: engine.Standing) -> str:
    """Объяснение порядка: результат с пометкой судьи стоит после всех чистых времён."""
    entry = standing.entry
    if standing.waiting or not entry.judge_note or entry.auto_finalized:
        return ""
    if standing.value.is_time:
        return "ниже чистых времён: пометка судьи"
    return ""


def _group_view(group: Group, *, highlight: str, etas=None) -> GroupView:
    phase = engine.group_phase(group)
    view = GroupView(
        key=group.key,
        sheet_name=group.sheet_name,
        name=group.name,
        phase=phase.value,
        phase_label=PHASE_LABELS[phase],
        is_single_run=group.is_single_run,
        rows=_rows(group, engine.standings(group), highlight=highlight, etas=etas),
        finishers=sum(e.is_finished() and e.effective_total().is_time for e in group.entries),
        out=sum(e.is_finished() and not e.effective_total().is_time for e in group.entries),
        waiting=sum(not e.is_finished() for e in group.entries),
        total=len(group.entries),
    )
    if phase is engine.Phase.RUN2 or (phase is engine.Phase.COMPLETED and not group.is_single_run):
        view.run1_rows = _rows(group, engine.run1_standings(group), highlight=highlight, with_analytics=False)
    return view


def _finisher(event: ResultsEvent, protocol: Protocol) -> Finisher | None:
    latest, latest_at, latest_run = None, None, 0
    for row in event.entries.filter(removed=False).only("key", "run1_seen_at", "run2_seen_at"):
        for run in (1, 2):
            at = getattr(row, f"run{run}_seen_at")
            if at and (latest_at is None or at > latest_at):
                latest, latest_at, latest_run = row.key, at, run
    if latest is None:
        return None
    entry = next((e for e in protocol.entries if e.key == latest), None)
    if entry is None:
        return None
    group = protocol.group(entry.group_key)
    place = next((s.place for s in engine.standings(group) if s.entry.key == entry.key), None) if group else None
    return Finisher(
        start_number=entry.start_number,
        full_name=entry.full_name,
        club=entry.club,
        run_number=latest_run,
        value=entry.run(latest_run).display() or "—",
        place=place,
    )


def _etas(event: ResultsEvent, group: Group, run_number: int, now: datetime) -> dict[str, datetime]:
    seen = {}
    for row in event.entries.filter(group__key=group.key, removed=False).only("key", "run1_seen_at", "run2_seen_at"):
        for run in (1, 2):
            if at := getattr(row, f"run{run}_seen_at"):
                seen[(row.key, run)] = at
    return engine.estimate_starts(list(group.entries), run_number=run_number, seen_at=seen, now=now)


def build_state(event: ResultsEvent, now: datetime | None = None) -> LiveState:
    now = now or timezone.now()
    protocol = effective_protocol(event, now)
    highlight = _norm(event.highlight_club)
    focus = engine.Focus.from_dict(event.focus).current
    focus_view = None
    groups = []
    for group in protocol.groups:
        etas = None
        if (
            focus
            and group.key == focus.group_key
            and engine.group_phase(group) in (engine.Phase.RUN1, engine.Phase.RUN2)
        ):
            etas = _etas(event, group, focus.run_number, now)
        view = _group_view(group, highlight=highlight, etas=etas)
        groups.append(view)
        if focus and group.key == focus.group_key:
            focus_view = view
    completed = bool(protocol.groups) and all(g.completed() for g in protocol.groups)
    finished = completed or event.status == ResultsEvent.Status.FINISHED
    return LiveState(
        event=event,
        title=event.title or protocol.title,
        date=protocol.event_date,
        status_label=event.get_status_display(),
        focus_group=focus_view,
        focus_run=focus.run_number if focus else 0,
        groups=groups,
        finisher=_finisher(event, protocol),
        club_stats=engine.club_stats(protocol.groups) if finished else [],
        completed=completed,
        finished=finished,
        started=protocol.has_progress(),
        generated_at=now,
    )
