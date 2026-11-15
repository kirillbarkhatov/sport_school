"""Тесты чистого ядра: разбор значений и протоколов, места, фазы, автофинализация, фокус, прогноз."""

from datetime import datetime, timedelta

import pytest

from results.core import engine
from results.core.diff import diff, run_changes
from results.core.parsing import ColumnMap, detect_columns, parse_protocol
from results.core.protocol import Entry, Group, Protocol
from results.core.values import Kind, RunValue, format_gap, format_ms, parse_value

from .fixtures import protocols

NOW = datetime(2026, 2, 15, 12, 0, 0)


# --- values ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "kind", "ms", "status"),
    [
        ("34.56", Kind.TIME, 34560, ""),
        ("34,5", Kind.TIME, 34500, ""),
        ("1:02.3", Kind.TIME, 62300, ""),
        ("1:02,50", Kind.TIME, 62500, ""),
        (35.1, Kind.TIME, 35100, ""),
        ("100", Kind.STATUS, None, "DNS"),
        ("150", Kind.STATUS, None, "DNF"),
        ("200", Kind.STATUS, None, "DSQ"),
        ("dnf", Kind.STATUS, None, "DNF"),
        ("МВ1", Kind.STATUS, None, "МВ"),
        ("", Kind.EMPTY, None, ""),
        (None, Kind.EMPTY, None, ""),
        ("0", Kind.EMPTY, None, ""),
        ("снят", Kind.STATUS, None, "СНЯТ"),
    ],
)
def test_parse_value(raw, kind, ms, status):
    value = parse_value(raw)
    assert (value.kind, value.ms, value.status) == (kind, ms, status)


def test_format_ms_and_gap():
    assert format_ms(34560) == "34.56"
    assert format_ms(62300) == "1:02.30"
    assert format_ms(None) == ""
    assert format_gap(0) == "0.00"
    assert format_gap(1230) == "+1.23"


# --- parsing --------------------------------------------------------------------------------------


def _by_name(protocol: Protocol) -> dict[str, Entry]:
    return {e.full_name: e for e in protocol.entries}


def test_parse_format_a_two_runs():
    protocol = parse_protocol(protocols.format_a_two_runs())
    assert protocol.title == "КУБОК ТЕСТОВОЙ ОБЛАСТИ"
    assert protocol.event_date == "15.02.2026"
    assert [g.name for g in protocol.groups] == ["Девочки 2014-2015 г.р.", "Мальчики 2014-2015 г.р."]
    entries = _by_name(protocol)
    assert len(entries) == 8
    anna = entries["Альфа Анна"]
    assert (anna.start_number, anna.club, anna.birth_year, anna.runs_count) == (1, "СШ Север", 2014, 2)
    assert anna.run1.ms == 35100 and anna.run2.ms == 36000 and anna.total.ms == 71100
    assert entries["Гамма Галина"].run1.status == "DNS"
    assert entries["Дельта Дарья"].run2.status == "DNF"
    assert entries["Зета Захар"].judge_note == "касание флага"
    assert entries["Эта Эрик"].run1.ms == 62500
    assert entries["Тета Тимур"].run1.is_empty
    assert protocol.warnings == ()


def test_parse_format_a_lite_single_run():
    protocol = parse_protocol(protocols.format_a_lite_single_run())
    assert protocol.event_date == "07.04.2026"
    assert protocol.competition_format() == "single_run"
    group = protocol.groups[0]
    assert group.is_single_run and len(group.entries) == 3
    assert all(e.runs_count == 1 for e in group.entries)


def test_parse_format_b_split_names_and_repeated_headers():
    protocol = parse_protocol(protocols.format_b_split_names())
    assert protocol.event_date == "02.04.2026"
    assert [g.name for g in protocol.groups] == ["Юноши 2012-2013 г.р.", "Девушки 2012-2013 г.р."]
    entries = _by_name(protocol)
    assert set(entries) == {"Мю Марк", "Ню Никита", "Кси Ксения", "Омикрон Ольга"}
    mark = entries["Мю Марк"]
    assert mark.run1.ms == 40000 and mark.run2.ms == 41000 and mark.total.ms == 81000


def test_parse_legacy_falls_back_with_warning():
    protocol = parse_protocol(protocols.legacy_without_header())
    assert {e.full_name for e in protocol.entries} == {"Пи Павел", "Ро Роман"}
    assert protocol.groups[0].name == "Группа А"
    assert any("шапка не найдена" in w for w in protocol.warnings)


def test_manual_column_map_overrides_detection():
    sheets = protocols.legacy_without_header()
    cmap = ColumnMap(start_number=0, full_name=1, club=2, run1=3, run2=None, total=None, header_row=6)
    protocol = parse_protocol(sheets, {"Лист1": cmap})
    assert protocol.warnings == ()
    assert all(e.runs_count == 1 for e in protocol.entries)


def test_column_map_roundtrip():
    cmap = detect_columns(protocols.format_a_two_runs()["СЛГ"])
    assert cmap is not None and ColumnMap.from_dict(cmap.to_dict()) == cmap


# --- engine: места --------------------------------------------------------------------------------


def _entry(n, run1="", run2="", total="", note="", runs=2, group="G", sheet="S", row=None):
    return Entry(
        sheet_name=sheet,
        group_name=group,
        start_number=n,
        full_name=f"P{n}",
        run1=parse_value(run1),
        run2=parse_value(run2),
        total=parse_value(total),
        runs_count=runs,
        judge_note=note,
        sheet_row=row if row is not None else n,
    )


def _group(*entries, name="G", sheet="S"):
    return Group(sheet_name=sheet, name=name, entries=tuple(entries))


def test_standings_after_run1_orders_times_notes_statuses_waiting():
    group = _group(
        _entry(1, "35.0"), _entry(2, "34.0"), _entry(3, "33.0", note="касание"), _entry(4, "DNF"), _entry(5)
    )
    result = engine.standings(group)
    assert [s.entry.start_number for s in result] == [2, 1, 3, 4, 5]
    assert [s.gap_ms for s in result[:2]] == [0, 1000]
    assert result[-1].waiting


def test_standings_in_run2_ranks_finished_by_total_and_keeps_waiting_last():
    group = _group(
        _entry(1, "35.0", "36.0"),  # 71.0
        _entry(2, "34.0", "38.0"),  # 72.0
        _entry(3, "33.0"),  # ждёт 2-й заезд
        _entry(4, "DNS"),
    )
    result = engine.standings(group)
    assert [s.entry.start_number for s in result] == [1, 2, 4, 3]
    assert result[1].gap_ms == 1000
    assert result[3].waiting and result[3].gap_ms is None


def test_effective_total_status_dominates_and_sum_fallback():
    assert _entry(1, "35.0", "DSQ", "71.0").effective_total().status == "DSQ"
    assert _entry(1, "35.0", "36.5").effective_total().ms == 71500
    assert _entry(1, "35.0", runs=1).effective_total().ms == 35000


def test_place_changes_and_best_runs():
    group = _group(_entry(1, "34.0", "37.0"), _entry(2, "35.0", "35.0"), _entry(3, "36.0", "36.5"))
    # после 1-го: 1,2,3; итог: №2=70.0, №1=71.0, №3=72.5
    assert engine.place_changes(group) == {"S|G|2|P2": 1, "S|G|1|P1": -1, "S|G|3|P3": 0}
    assert engine.best_run_keys(group, 1) == {"S|G|1|P1"}
    assert engine.best_run_keys(group, 2) == {"S|G|2|P2"}


def test_run1_outlook_after_run1_closed():
    group = _group(_entry(1, "34.0"), _entry(2, "34.1"), _entry(3, "40.0"), _entry(4, "DNF"))
    outlook = engine.run1_outlook(group)
    assert set(outlook) == {"S|G|1|P1", "S|G|2|P2", "S|G|3|P3"}
    assert outlook["S|G|1|P1"].label == "Лидер под давлением"
    assert outlook["S|G|3|P3"].p_up < 0.05


# --- engine: фазы и автофинализация ---------------------------------------------------------------


def test_phases():
    assert engine.group_phase(_group(_entry(1), _entry(2))) is engine.Phase.NOT_STARTED
    assert engine.group_phase(_group(_entry(1, "30"), _entry(2))) is engine.Phase.RUN1
    assert engine.group_phase(_group(_entry(1, "30"), _entry(2, "DNS"))) is engine.Phase.BREAK
    assert engine.group_phase(_group(_entry(1, "30", "31"), _entry(2, "31"))) is engine.Phase.RUN2
    assert engine.group_phase(_group(_entry(1, "30", "31"), _entry(2, "DNS"))) is engine.Phase.COMPLETED


def test_auto_finalize_when_next_group_on_same_sheet_started():
    g1 = _group(_entry(1, "30"), _entry(2, "31"), _entry(3), name="G1")
    g2 = _group(_entry(11, "29", group="G2"), _entry(12, group="G2"), name="G2")
    protocol = Protocol(groups=(g1, g2))
    result = engine.auto_finalize(protocol, now=NOW, last_update_at={})
    p3 = next(e for e in result.groups[0].entries if e.start_number == 3)
    assert p3.run1.status == "DNS" and p3.auto_finalized and "автофинализация" in p3.judge_note
    assert result.groups[0].run_closed(1)
    assert result.groups[1] == g2  # текущая группа не трогается


def test_auto_finalize_by_timeout_only_with_few_missing():
    group = _group(_entry(1, "30"), _entry(2), _entry(3), _entry(4))
    protocol = Protocol(groups=(group,))
    old = {group.key: NOW - timedelta(minutes=10)}
    rules = engine.FinalizeRules(timeout_sec=300, max_missing=2)
    assert engine.auto_finalize(protocol, now=NOW, last_update_at=old, rules=rules) == protocol  # не хватает 3
    rules = engine.FinalizeRules(timeout_sec=300, max_missing=3)
    finalized = engine.auto_finalize(protocol, now=NOW, last_update_at=old, rules=rules)
    assert finalized.groups[0].run_closed(1)
    fresh = {group.key: NOW - timedelta(minutes=1)}
    assert engine.auto_finalize(protocol, now=NOW, last_update_at=fresh, rules=rules) == protocol


def test_auto_finalize_run2_ignores_athletes_with_run1_status():
    g1 = _group(_entry(1, "30", "31"), _entry(2, "DNF"), _entry(3, "32"), name="G1")
    g2 = _group(_entry(11, "29", "30", group="G2"), name="G2")
    result = engine.auto_finalize(Protocol(groups=(g1, g2)), now=NOW, last_update_at={})
    entries = {e.start_number: e for e in result.groups[0].entries}
    assert entries[3].run2.status == "DNS"
    assert entries[2].run2.is_empty and not entries[2].auto_finalized


# --- engine: фокус --------------------------------------------------------------------------------


def _two_group_sheet(g1_runs, g2_runs, sheet="S"):
    g1 = _group(*[_entry(i + 1, *r, group="G1", sheet=sheet) for i, r in enumerate(g1_runs)], name="G1", sheet=sheet)
    g2 = _group(*[_entry(i + 11, *r, group="G2", sheet=sheet) for i, r in enumerate(g2_runs)], name="G2", sheet=sheet)
    return g1, g2


def _candidates(protocol, *pairs):
    """pairs: (start_number, run) → кандидаты фокуса из протокола."""
    by_number = {e.start_number: e for e in protocol.entries}
    return engine.focus_candidates([(by_number[n], run) for n, run in pairs])


def test_focus_follows_groups_within_same_run():
    g1, g2 = _two_group_sheet([("30",), ("31",)], [("29",), ()])
    protocol = Protocol(groups=(g1, g2))
    focus = engine.next_focus(protocol, engine.Focus(), _candidates(protocol, (2, 1)))
    assert focus.current.group_key == "S|G1" and focus.current.run_number == 1
    focus = engine.next_focus(protocol, focus, _candidates(protocol, (11, 1)))
    assert focus.current.group_key == "S|G2" and focus.deferred is None


def test_focus_defers_run2_until_whole_sheet_run1_closed():
    """Регрессия «фокус между заездами»: 2-й заезд первой группы не перехватывает фокус, пока едет 1-й."""
    g1, g2 = _two_group_sheet([("30", "31"), ("31",)], [("29",), ()])
    protocol = Protocol(groups=(g1, g2))
    start = engine.Focus(current=engine.FocusTarget("S", "S|G2", 1))
    focus = engine.next_focus(protocol, start, _candidates(protocol, (1, 2)))
    assert focus.current.group_key == "S|G2" and focus.current.run_number == 1
    assert focus.deferred == engine.FocusTarget("S", "S|G1", 2)

    # последний участник G2 проехал 1-й заезд → лист закрыт → фокус переходит на отложенный 2-й заезд
    g1, g2 = _two_group_sheet([("30", "31"), ("31",)], [("29",), ("30",)])
    protocol = Protocol(groups=(g1, g2))
    focus = engine.next_focus(protocol, focus, _candidates(protocol, (12, 1)))
    assert focus.current == engine.FocusTarget("S", "S|G1", 2) and focus.deferred is None


def test_focus_single_run_group_closes_on_its_own():
    single = _group(_entry(1, "40", runs=1, group="Kids", sheet="K"), name="Kids", sheet="K")
    g1, g2 = _two_group_sheet([("30",)], [()], sheet="S")
    protocol = Protocol(groups=(single, g1, g2))
    start = engine.Focus(current=engine.FocusTarget("K", "K|Kids", 1))
    focus = engine.next_focus(protocol, start, _candidates(protocol, (1, 1)))
    # однозаездная группа закрыта целиком → можно сразу на другой лист
    assert focus.current == engine.FocusTarget("S", "S|G1", 1)


def test_focus_initializes_from_current_group_and_survives_removed_group():
    g1, g2 = _two_group_sheet([("30", "31"), ("31",)], [(), ()])
    protocol = Protocol(groups=(g1, g2))
    focus = engine.next_focus(protocol, engine.Focus(), [])
    assert focus.current == engine.FocusTarget("S", "S|G1", 2)
    gone = engine.Focus(current=engine.FocusTarget("S", "S|Удалённая", 1))
    assert engine.next_focus(protocol, gone, []).current == engine.FocusTarget("S", "S|G1", 2)


def test_focus_roundtrip_dict():
    focus = engine.Focus(current=engine.FocusTarget("S", "S|G", 2), deferred=engine.FocusTarget("T", "T|H", 1))
    assert engine.Focus.from_dict(focus.to_dict()) == focus
    assert engine.Focus.from_dict(None) == engine.Focus()


def test_status_does_not_move_focus():
    g1, g2 = _two_group_sheet([("30",), ("DNS",)], [(), ()])
    protocol = Protocol(groups=(g1, g2))
    assert _candidates(protocol, (2, 1)) == []


# --- engine: прогноз и клубы ----------------------------------------------------------------------


def test_estimate_starts_uses_base_tempo_then_observed_intervals():
    entries = [_entry(i, "40.0" if i <= 4 else "") for i in range(1, 8)]
    seen = {(e.key, 1): NOW - timedelta(seconds=60 - 15 * i) for i, e in enumerate(entries[:4])}
    estimates = engine.estimate_starts(entries, run_number=1, seen_at=seen, now=NOW)
    assert list(estimates) == [e.key for e in entries[4:]]
    first = (estimates[entries[4].key] - NOW).total_seconds()
    # база 20 c (40/2), наблюдаемый интервал 15 c, вес 3/6 → 17.5 c
    assert first == pytest.approx(17.5)


def test_club_stats_counts_podiums():
    group = _group(_entry(1, "30", "30"), _entry(2, "31", "31"), _entry(3, "DNS"))
    group = group.with_entries(tuple(e.with_runs(club="A" if e.start_number < 3 else "B") for e in group.entries))
    stats = {s.club: s for s in engine.club_stats([group])}
    assert (stats["A"].participants, stats["A"].finishers, stats["A"].podiums) == (2, 2, 2)
    assert stats["B"].finishers == 0


# --- diff -----------------------------------------------------------------------------------------


def test_diff_reports_added_and_changed_runs():
    before = parse_protocol(protocols.format_a_two_runs())
    sheets = protocols.format_a_two_runs()
    sheets["СЛГ"][12][5] = "34.00"  # 2-й заезд Эпсилона
    after = parse_protocol(sheets)
    changes = diff(before, after)
    assert [(c.entry.full_name, c.field, c.after) for c in changes] == [("Эпсилон Егор", "run2", "34.00")]
    assert run_changes(changes)[0][1] == 2
    initial = diff(None, before)
    assert sum(c.field == "added" for c in initial) == 8


def test_runvalue_constructors():
    assert RunValue.time(1500).display() == "1.50"
    assert RunValue.of_status("DNF").display() == "DNF"
