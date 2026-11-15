"""Связка участников протокола со спортсменами школы (school.Athlete).

Сопоставление по «фамилия + имя» (без учёта регистра и ё/е); если в протоколе есть год рождения —
он должен совпасть. Связываем только при единственном кандидате; ручные связи не перезаписываются.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from django.db import transaction

from school.models import Athlete

from .models import ResultEntry, ResultsEvent


def normalize_name(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").replace("-", " ").split())


def _name_keys(full_name: str) -> list[str]:
    """«Иванов Иван Петрович» → ["иванов иван"], «Иван Иванов» тоже пробуем в обратном порядке."""
    parts = normalize_name(full_name).split()
    if len(parts) < 2:
        return []
    return [f"{parts[0]} {parts[1]}", f"{parts[1]} {parts[0]}"]


@dataclass
class LinkReport:
    linked: int = 0
    ambiguous: int = 0
    not_found: int = 0
    kept_manual: int = 0


def link_event_entries(event: ResultsEvent) -> LinkReport:
    index: dict[str, list[tuple[int, int | None]]] = defaultdict(list)
    for athlete_id, surname, name, birth in Athlete.objects.values_list(
        "id", "person__surname", "person__name", "person__date_of_birth"
    ):
        key = normalize_name(f"{surname} {name}")
        index[key].append((athlete_id, birth.year if birth else None))

    report = LinkReport()
    with transaction.atomic():
        for entry in event.entries.filter(removed=False).select_for_update():
            if entry.link_method == ResultEntry.LinkMethod.MANUAL:
                report.kept_manual += 1
                continue
            candidates: dict[int, int | None] = {}
            for key in _name_keys(entry.full_name):
                for athlete_id, year in index.get(key, []):
                    candidates[athlete_id] = year
            if entry.birth_year:
                candidates = {a: y for a, y in candidates.items() if y in (None, entry.birth_year)}
            if len(candidates) == 1:
                athlete_id = next(iter(candidates))
                if entry.athlete_id != athlete_id:
                    entry.athlete_id = athlete_id
                    entry.link_method = ResultEntry.LinkMethod.AUTO
                    entry.save(update_fields=["athlete", "link_method"])
                report.linked += 1
            elif candidates:
                report.ambiguous += 1
            else:
                report.not_found += 1
    return report
