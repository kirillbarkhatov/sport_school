"""Разбор листов протокола в Protocol.

Колонки ищутся по заголовкам (форматы «A» — одна колонка «Фамилия, Имя»; «B» — раздельные фамилия/имя,
заезды часто во второй строке шапки). Если автоопределение ошибается, разметку колонок можно задать
вручную (ColumnMap хранится в БД у источника) — тогда эвристики шапки не используются.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, fields

from .protocol import Entry, Group, Protocol
from .values import EMPTY, RunValue, parse_value

Rows = list[list[object]]

_START_NUMBER = re.compile(r"^\d+(?:[.,]0+)?$")
_YEAR = re.compile(r"^(19|20)\d{2}$")
_DATE_FULL = re.compile(r"\b(\d{2})[.\-/](\d{2})[.\-/](\d{4})\b")
_DATE_SHORT = re.compile(r"\b(\d{2})[.\-/](\d{2})[.\-/](\d{2})\b")
_DATE_TEXT = re.compile(
    r"\b(\d{1,2})\s+(январ[яь]|феврал[яь]|март[а]?|апрел[яь]|ма[йя]|июн[яь]|июл[яь]|август[а]?|"
    r"сентябр[яь]|октябр[яь]|ноябр[яь]|декабр[яь])\s+(\d{4})\b",
    flags=re.IGNORECASE,
)
_MONTHS = {
    "январ": 1,
    "феврал": 2,
    "март": 3,
    "апрел": 4,
    "май": 5,
    "мая": 5,
    "июн": 6,
    "июл": 7,
    "август": 8,
    "сентябр": 9,
    "октябр": 10,
    "ноябр": 11,
    "декабр": 12,
}
_GROUP_TOKENS = ("девоч", "мальч", "девуш", "юнош", "юниор", "группа", "год рождения", "мужчин", "женщин")
_TITLE_TOKENS = ("кубок", "первенств", "чемпионат", "старт", "этап", "тур", "мемориал", "закрытие", "открытие")
HEADER_SCAN_ROWS = 40
DEFAULT_GROUP = "Без группы"


@dataclass(frozen=True)
class ColumnMap:
    """Индексы колонок (с нуля). header_row — номер строки шапки (с единицы), 0 — шапки нет."""

    start_number: int
    club: int
    run1: int
    full_name: int | None = None
    surname: int | None = None
    first_name: int | None = None
    run2: int | None = None
    total: int | None = None
    birth_year: int | None = None
    header_row: int = 0

    @property
    def runs_count(self) -> int:
        return 2 if self.run2 is not None else 1

    def to_dict(self) -> dict[str, int | None]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ColumnMap:
        names = {f.name for f in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in names})


# --- Вспомогательные функции ячеек ---------------------------------------------------------------


def cell(row: list[object], index: int | None) -> str:
    if index is None or index < 0 or index >= len(row):
        return ""
    value = row[index]
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _low_cells(row: list[object]) -> list[str]:
    return [cell(row, index).lower() for index in range(len(row))]


def _find(cells: list[str], predicate: Callable[[str], bool], start: int = 0) -> int | None:
    for index in range(start, len(cells)):
        if cells[index] and predicate(cells[index]):
            return index
    return None


def _is_start_header(value: str) -> bool:
    return (
        "старт номер" in value or "стартовый номер" in value or ("ст" in value and ("№" in value or "номер" in value))
    )


def _is_run_header(value: str, number: int) -> bool:
    return str(number) in value and ("заезд" in value or "трас" in value or "попыт" in value)


def _is_total_header(value: str) -> bool:
    return any(token in value for token in ("результ", "итог", "сумма"))


def _is_club_header(value: str) -> bool:
    return any(token in value for token in ("фсо", "клуб", "команда", "организац", "регион"))


def _is_birth_header(value: str) -> bool:
    return "г.р" in value or "год рожд" in value or value in {"гр", "г/р"}


def parse_start_number(value: str) -> int | None:
    normalized = value.replace(",", ".")
    if not _START_NUMBER.match(normalized):
        return None
    return int(float(normalized))


# --- Поиск шапки ---------------------------------------------------------------------------------


def _header_a(row: list[object], row_index: int) -> ColumnMap | None:
    """Формат A: колонка «Фамилия, Имя» и «1 заезд / 1 трасса» в одной строке."""
    cells = _low_cells(row)
    full_name = _find(cells, lambda v: "фамилия" in v and "имя" in v)
    run1 = _find(cells, lambda v: _is_run_header(v, 1))
    start = _find(cells, _is_start_header)
    club = _find(cells, _is_club_header)
    if None in (full_name, run1, start, club):
        return None
    run2 = _find(cells, lambda v: _is_run_header(v, 2))
    total = _find(cells, _is_total_header)
    if total is None and run1 + 1 < len(cells) and run2 is None:
        total = run1 + 1  # однозаездный протокол: результат сразу после заезда
    return ColumnMap(
        start_number=start,
        full_name=full_name,
        club=club,
        run1=run1,
        run2=run2,
        total=total,
        birth_year=_find(cells, _is_birth_header),
        header_row=row_index,
    )


def _header_b(rows: Rows, row_index: int) -> ColumnMap | None:
    """Формат B: «Старт номер», раздельные «Фамилия» и «Имя», заезды могут быть строкой ниже."""
    cells = _low_cells(rows[row_index - 1])
    start = _find(cells, _is_start_header)
    surname = _find(cells, lambda v: "фамил" in v)
    first_name = _find(cells, lambda v: v.startswith("имя") or ("имя" in v and "фамил" not in v))
    club = _find(cells, _is_club_header)
    run1 = _find(cells, lambda v: _is_run_header(v, 1))
    run2 = _find(cells, lambda v: _is_run_header(v, 2))
    total = _find(cells, _is_total_header)
    for offset in (1, 2):
        if run1 is not None and run2 is not None:
            break
        if row_index - 1 + offset >= len(rows):
            break
        sub = _low_cells(rows[row_index - 1 + offset])
        run1 = run1 if run1 is not None else _find(sub, lambda v: _is_run_header(v, 1))
        run2 = run2 if run2 is not None else _find(sub, lambda v: _is_run_header(v, 2))
    if run1 is None:
        attempts = _find(cells, lambda v: "попыт" in v)
        if attempts is not None:
            run1, run2 = attempts, attempts + 1
    if total is None and run2 is not None:
        total = run2 + 1
    if None in (start, surname, first_name, club, run1):
        return None
    return ColumnMap(
        start_number=start,
        surname=surname,
        first_name=first_name,
        club=club,
        run1=run1,
        run2=run2,
        total=total,
        birth_year=_find(cells, _is_birth_header),
        header_row=row_index,
    )


def _is_header_row(row: list[object]) -> bool:
    text = " | ".join(v for v in _low_cells(row) if v)
    return ("фамил" in text and ("заезд" in text or "трас" in text or "старт номер" in text)) or (
        "попыт" in text and "команда" in text
    )


def detect_columns(rows: Rows) -> ColumnMap | None:
    for row_index, row in enumerate(rows[:HEADER_SCAN_ROWS], start=1):
        if not _is_header_row(row):
            continue
        cmap = _header_a(row, row_index) or _header_b(rows, row_index)
        if cmap is not None:
            return cmap
    return None


LEGACY_COLUMNS = ColumnMap(start_number=0, full_name=1, club=2, run1=3, run2=4, total=5, header_row=6)


# --- Метаданные листа ----------------------------------------------------------------------------


def _meta_lines(rows: Rows, limit: int) -> list[str]:
    lines = []
    for row in rows[:limit]:
        first = next((cell(row, i) for i in range(min(len(row), 3)) if cell(row, i)), "")
        if not first or _is_header_row(row):
            continue
        low = first.lower()
        if "усл. обознач" in low or "предварительные результаты" in low:
            continue
        lines.append(first)
    return lines


def extract_date(lines: Iterable[str]) -> str:
    joined = " ".join(lines)
    if match := _DATE_FULL.search(joined):
        return f"{match.group(1)}.{match.group(2)}.{match.group(3)}"
    if match := _DATE_SHORT.search(joined):
        return f"{match.group(1)}.{match.group(2)}.20{match.group(3)}"
    if match := _DATE_TEXT.search(joined):
        token = match.group(2).lower()
        month = next((num for prefix, num in _MONTHS.items() if token.startswith(prefix)), 0)
        if month:
            return f"{int(match.group(1)):02d}.{month:02d}.{match.group(3)}"
    return ""


def extract_title(lines: list[str]) -> str:
    best, best_score = "", -999
    for line in lines[:8]:
        low = line.lower()
        score = 0
        if any(token in low for token in _TITLE_TOKENS):
            score += 6
        if "соревнования по" in low:
            score -= 5
        if any(token in low for token in ("область", "гк ", "ск ", "д. ")):
            score -= 3
        if any(token in low for token in _GROUP_TOKENS) or "слалом" in low:
            score -= 2
        if "протокол" in low and "кубок" not in low:
            score -= 2
        if extract_date([line]):
            score -= 1
        if line.isupper():
            score += 1
        if score > best_score:
            best, best_score = line, score
    return best.strip(' "«»')


# --- Строки участников и групп --------------------------------------------------------------------


def _group_title(row: list[object], cmap: ColumnMap) -> str | None:
    """Строка-разделитель группы: «Девочки 2015-2016 г.р.» или текст без номера и без данных рядом."""
    for index in range(min(len(row), 3)):
        value = cell(row, index)
        if value and any(token in value.lower() for token in _GROUP_TOKENS):
            if parse_start_number(cell(row, cmap.start_number)) is None:
                return value
    first = cell(row, 0)
    if first and parse_start_number(first) is None and not any(cell(row, i) for i in range(1, 4)):
        return first
    return None


def _group_above_header(rows: Rows, cmap: ColumnMap) -> str:
    """В формате B название первой группы стоит над шапкой — ищем его в 3 строках выше."""
    for index in range(cmap.header_row - 2, max(cmap.header_row - 5, -1), -1):
        row = rows[index]
        for col in range(min(len(row), 3)):
            value = cell(row, col)
            if value and any(token in value.lower() for token in _GROUP_TOKENS):
                return value
    return DEFAULT_GROUP


def _full_name(row: list[object], cmap: ColumnMap) -> str:
    if cmap.full_name is not None:
        return " ".join(cell(row, cmap.full_name).split())
    parts = [cell(row, cmap.surname), cell(row, cmap.first_name)]
    return " ".join(" ".join(part for part in parts if part).split())


def _birth_year(row: list[object], cmap: ColumnMap) -> int | None:
    value = cell(row, cmap.birth_year)
    return int(value) if _YEAR.match(value) else None


def _judge_note(row: list[object], start: int) -> str:
    """Пометка судьи справа от итога: статус или текст (места и числа пропускаются)."""
    for index in range(max(start, 0), len(row)):
        value = cell(row, index)
        if not value:
            continue
        parsed = parse_value(value)
        if parsed.is_status and parsed.status in ("DNS", "DNF", "DSQ"):
            return parsed.status
        if parse_start_number(value) is not None or parsed.is_time:
            continue
        return value
    return ""


def parse_sheet(sheet_name: str, rows: Rows, cmap: ColumnMap | None = None) -> tuple[list[Entry], list[str]]:
    """Возвращает участников листа и предупреждения. cmap=None — автоопределение колонок."""
    warnings: list[str] = []
    auto = cmap is None
    if cmap is None:
        cmap = detect_columns(rows)
        if cmap is None:
            cmap = LEGACY_COLUMNS
            warnings.append(f"{sheet_name}: шапка не найдена, использована разметка по умолчанию (A–F с 7-й строки)")

    entries: list[Entry] = []
    group_name = _group_above_header(rows, cmap)
    note_from = (cmap.total if cmap.total is not None else (cmap.run2 or cmap.run1)) + 1

    for row_index in range(cmap.header_row + 1, len(rows) + 1):
        row = rows[row_index - 1]
        if not any(cell(row, i) for i in range(len(row))):
            continue
        if auto and _is_header_row(row):
            # повтор шапки перед каждой группой — перечитываем колонки (формат B)
            cmap = _header_a(row, row_index) or _header_b(rows, row_index) or cmap
            continue
        start_number = parse_start_number(cell(row, cmap.start_number))
        if start_number is None:
            title = _group_title(row, cmap)
            if title:
                group_name = title
            continue
        full_name = _full_name(row, cmap)
        if not full_name:
            continue
        run2 = parse_value(cell(row, cmap.run2)) if cmap.run2 is not None else EMPTY
        total: RunValue = parse_value(cell(row, cmap.total)) if cmap.total is not None else EMPTY
        entries.append(
            Entry(
                sheet_name=sheet_name,
                group_name=group_name,
                start_number=start_number,
                full_name=full_name,
                club=" ".join(cell(row, cmap.club).split()),
                birth_year=_birth_year(row, cmap),
                run1=parse_value(cell(row, cmap.run1)),
                run2=run2,
                total=total,
                runs_count=cmap.runs_count,
                judge_note=_judge_note(row, note_from),
                sheet_row=row_index,
            )
        )
    if not entries:
        warnings.append(f"{sheet_name}: участники не найдены")
    return entries, warnings


def parse_protocol(sheets: dict[str, Rows], mappings: dict[str, ColumnMap] | None = None) -> Protocol:
    """Все листы → Protocol. Пустые листы пропускаются, порядок групп — порядок появления."""
    mappings = mappings or {}
    groups: dict[str, list[Entry]] = {}
    group_sheets: dict[str, tuple[str, str]] = {}
    warnings: list[str] = []
    meta_lines: list[str] = []

    for sheet_name, rows in sheets.items():
        if not rows:
            continue
        cmap = mappings.get(sheet_name)
        entries, sheet_warnings = parse_sheet(sheet_name, rows, cmap)
        warnings.extend(sheet_warnings)
        header_row = (cmap or detect_columns(rows) or LEGACY_COLUMNS).header_row
        meta_lines.extend(_meta_lines(rows, header_row or 12))
        for entry in entries:
            groups.setdefault(entry.group_key, []).append(entry)
            group_sheets[entry.group_key] = (entry.sheet_name, entry.group_name)

    return Protocol(
        groups=tuple(
            Group(sheet_name=group_sheets[key][0], name=group_sheets[key][1], entries=tuple(items), order_index=i)
            for i, (key, items) in enumerate(groups.items())
        ),
        title=extract_title(meta_lines),
        event_date=extract_date(meta_lines),
        warnings=tuple(warnings),
    )
