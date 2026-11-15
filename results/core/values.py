"""Значение заезда: время (в миллисекундах), статус или пусто."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Kind(StrEnum):
    EMPTY = "empty"
    TIME = "time"
    STATUS = "status"


# Статусы, которые судьи ставят числом в колонку времени
STATUS_BY_NUMBER = {100: "DNS", 150: "DNF", 200: "DSQ"}
TERMINAL_STATUSES = ("DNS", "DNF", "DSQ")
# Порядок статусов в протоколе: не стартовал < не финишировал < дисквалифицирован < прочие
STATUS_ORDER = {"DNS": 1, "DNF": 2, "DSQ": 3}

_NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")
_MIN_SEC = re.compile(r"^(\d{1,2}):(\d{1,2}(?:[.,]\d{1,3})?)$")


@dataclass(frozen=True, slots=True)
class RunValue:
    kind: Kind = Kind.EMPTY
    ms: int | None = None
    status: str = ""
    raw: str = ""

    @property
    def is_empty(self) -> bool:
        return self.kind is Kind.EMPTY

    @property
    def is_time(self) -> bool:
        return self.kind is Kind.TIME

    @property
    def is_status(self) -> bool:
        return self.kind is Kind.STATUS

    def display(self) -> str:
        if self.is_time:
            return format_ms(self.ms)
        if self.is_status:
            return self.status
        return ""

    @classmethod
    def time(cls, ms: int, raw: str = "") -> RunValue:
        return cls(kind=Kind.TIME, ms=ms, raw=raw or format_ms(ms))

    @classmethod
    def of_status(cls, status: str, raw: str = "") -> RunValue:
        return cls(kind=Kind.STATUS, status=status, raw=raw or status)


EMPTY = RunValue()


def parse_value(raw: object) -> RunValue:
    """Разбор ячейки времени: «34.56», «1:02.3», «100» (DNS), «DNF», «МВ», произвольный текст = статус."""
    text = ("" if raw is None else str(raw)).strip().upper()
    if not text:
        return EMPTY
    if text in TERMINAL_STATUSES:
        return RunValue.of_status(text, raw=text)

    compact = re.sub(r"[^A-ZА-Я0-9]", "", text)
    if compact in {"MV", "МВ", "МВ1", "МВ2"}:
        return RunValue.of_status("МВ", raw=text)

    normalized = text.replace(",", ".")
    if _NUMBER.match(normalized):
        number = float(normalized)
        if number.is_integer() and int(number) in STATUS_BY_NUMBER:
            return RunValue.of_status(STATUS_BY_NUMBER[int(number)], raw=text)
        if number <= 0:
            return EMPTY
        return RunValue.time(round(number * 1000), raw=text)

    match = _MIN_SEC.match(text)
    if match:
        minutes = int(match.group(1))
        seconds = float(match.group(2).replace(",", "."))
        return RunValue.time(round((minutes * 60 + seconds) * 1000), raw=text)

    return RunValue.of_status(text, raw=text)


MANUAL_STATUSES = (*TERMINAL_STATUSES, "МВ")


class InvalidResult(ValueError):
    """Значение нельзя внести вручную (не время и не известный статус)."""


def validate_manual(raw: object) -> str:
    """Проверка ручного ввода. Пусто — очистить заезд; иначе время (34.56, 1:02.30) или DNS/DNF/DSQ/МВ
    (в том числе судейские коды 100/150/200). Возвращает нормализованное значение для хранения."""
    text = ("" if raw is None else str(raw)).strip()
    if not text:
        return ""
    value = parse_value(text)
    if value.is_time:
        return format_ms(value.ms)
    if value.is_status and value.status in MANUAL_STATUSES:
        return value.status
    raise InvalidResult(f"«{text}» — не время и не статус. Введите время (34.56 или 1:02.30) либо DNS, DNF, DSQ.")


def format_ms(ms: int | None) -> str:
    """34560 → «34.56», 62300 → «1:02.30»."""
    if ms is None:
        return ""
    total_cs = round(ms / 10)
    minutes, cs = divmod(total_cs, 6000)
    seconds = cs / 100
    if minutes:
        return f"{minutes}:{seconds:05.2f}"
    return f"{seconds:.2f}"


def format_gap(ms: int | None) -> str:
    """Отставание от лидера: «+1.23», для лидера «0.00»."""
    if ms is None:
        return ""
    if ms <= 0:
        return "0.00"
    return f"+{format_ms(ms)}"
