from __future__ import annotations

from dataclasses import dataclass


SEASON_START_MONTH = 7  # 1 July


DISCIPLINE_SL = "SL"
DISCIPLINE_GS = "GS"
DISCIPLINE_PSL = "PSL"
DISCIPLINE_PGS = "PGS"
DISCIPLINE_SL_GS = "SL+GS"


DISCIPLINE_CHOICES: list[tuple[str, str]] = [
    (DISCIPLINE_SL, "SL — Slalom (Слалом)"),
    (DISCIPLINE_GS, "GS — Giant Slalom (Гигантский слалом)"),
    (DISCIPLINE_PSL, "PSL — Parallel Slalom (Параллельный слалом)"),
    (DISCIPLINE_PGS, "PGS — Parallel Giant Slalom (Параллельный гигантский слалом)"),
    (DISCIPLINE_SL_GS, "SL + GS — Комбинация слалома и гиганта"),
]


DISCIPLINE_LABELS = dict(DISCIPLINE_CHOICES)


def normalize_discipline_value(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip().lower().replace("ё", "е")

    if any(marker in text for marker in ("sl+gs", "sl + gs", "sl/gs", "комбинац")):
        return DISCIPLINE_SL_GS
    if "паралл" in text and "гигант" in text:
        return DISCIPLINE_PGS
    if "паралл" in text and "слалом" in text:
        return DISCIPLINE_PSL
    if "слалом-гигант" in text or "гигантск" in text or " гигант" in f" {text}":
        return DISCIPLINE_GS
    if "слалом" in text or text == "sl":
        return DISCIPLINE_SL
    if text in {DISCIPLINE_SL.lower(), DISCIPLINE_GS.lower(), DISCIPLINE_PSL.lower(), DISCIPLINE_PGS.lower()}:
        return text.upper()
    return ""


def discipline_label(value: str | None) -> str:
    if not value:
        return ""
    code = normalize_discipline_value(value) or value
    return DISCIPLINE_LABELS.get(code, code)


def infer_season_start_year(*, date_from=None, fallback_year: int | None = None) -> int:
    if date_from is None:
        return fallback_year or 0
    return date_from.year if date_from.month >= SEASON_START_MONTH else (date_from.year - 1)


@dataclass(frozen=True)
class StandardCategory:
    code: str
    button_label: str
    age_min: int
    age_max: int | None
    male_label: str
    female_label: str


STANDARD_U_CATEGORIES: dict[str, StandardCategory] = {
    "AGE4": StandardCategory(code="AGE4", button_label="4г.", age_min=3, age_max=3, male_label="Мальчики", female_label="Девочки"),
    "AGE5": StandardCategory(code="AGE5", button_label="5л.", age_min=4, age_max=4, male_label="Мальчики", female_label="Девочки"),
    "AGE6": StandardCategory(code="AGE6", button_label="6л.", age_min=5, age_max=5, male_label="Мальчики", female_label="Девочки"),
    "U8": StandardCategory(code="U8", button_label="U8", age_min=6, age_max=7, male_label="Мальчики", female_label="Девочки"),
    "U10": StandardCategory(code="U10", button_label="U10", age_min=8, age_max=9, male_label="Мальчики", female_label="Девочки"),
    "U12": StandardCategory(code="U12", button_label="U12", age_min=10, age_max=11, male_label="Мальчики", female_label="Девочки"),
    "U14": StandardCategory(code="U14", button_label="U14", age_min=12, age_max=13, male_label="Юноши", female_label="Девушки"),
    "U16": StandardCategory(code="U16", button_label="U16", age_min=14, age_max=15, male_label="Юноши", female_label="Девушки"),
    "SENIOR": StandardCategory(code="SENIOR", button_label="Senior", age_min=16, age_max=None, male_label="Мужчины", female_label="Женщины"),
}
