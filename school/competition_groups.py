from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from django.utils import timezone

from .competition_standards import STANDARD_U_CATEGORIES, infer_season_start_year, normalize_discipline_value
from .models import Competition, CompetitionDocument, CompetitionScoringGroup, Document, DocumentAIAnalysis


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.lower().replace("ё", "е")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _tokenize(value: str) -> list[str]:
    tokenized = re.sub(r"[^\w\d/+-]+", " ", value.lower().replace("ё", "е"))
    return [token for token in tokenized.split() if token]


def _detect_gender_tokens(line: str) -> tuple[bool, bool]:
    text = _normalize_text(line)
    tokens = set(_tokenize(line))

    only_male_patterns = [
        r"\bтолько\s+(мужчины|мужчин|мальчики|юноши|парни)\b",
    ]
    only_female_patterns = [
        r"\bтолько\s+(женщины|женщин|девочки|девушки)\b",
    ]
    for pattern in only_male_patterns:
        if re.search(pattern, text):
            return True, False
    for pattern in only_female_patterns:
        if re.search(pattern, text):
            return False, True

    male_words = {
        "мужчина",
        "мужчины",
        "мужчин",
        "юноша",
        "юноши",
        "мальчик",
        "мальчики",
        "парень",
        "парни",
    }
    female_words = {
        "женщина",
        "женщины",
        "женщин",
        "девушка",
        "девушки",
        "девочка",
        "девочки",
    }
    has_male = any(word in text for word in male_words) or bool(tokens.intersection({"м", "муж", "ю", "юн"}))
    has_female = any(word in text for word in female_words) or bool(tokens.intersection({"ж", "жен", "д", "дев"}))

    if re.search(r"\bм\s*[/\-]\s*ж\b", text):
        has_male = True
        has_female = True
    if re.search(r"\b(юноши\s+и\s+девушки|мальчики\s*[,:]?\s*девочки)\b", text):
        has_male = True
        has_female = True

    # If gender is not explicit, create both groups by default.
    if not has_male and not has_female:
        return True, True
    if has_male and has_female:
        return True, True
    return has_male, has_female


def _extract_year_range(line: str) -> tuple[int | None, int | None]:
    text = _normalize_text(line)

    # 2018-2019, 2018/2019, 2018 — 2019
    pair_match = re.search(r"\b((?:19|20)\d{2})\s*[-–—/]\s*((?:19|20)\d{2})\b", text)
    if pair_match:
        y1 = int(pair_match.group(1))
        y2 = int(pair_match.group(2))
        return max(y1, y2), min(y1, y2)

    # 2010 и старше / 2010 г.р. и старше / от 2010
    open_match = re.search(r"\b((?:19|20)\d{2})\b(?:\s*г\.?р\.?)?\s*(?:и\s*старше|и\s*стар\w+)?", text)
    if open_match and ("старш" in text or text.startswith("от ")):
        return int(open_match.group(1)), None

    return None, None


def _extract_discipline(line: str) -> str:
    return (normalize_discipline_value(line) or "")[:100]


def _group_title(*, raw_text: str, gender_scope: str, birth_year_from: int | None, birth_year_to: int | None) -> str:
    prefix = "Мужчины" if gender_scope == CompetitionScoringGroup.GenderScope.MALE else "Женщины"
    if birth_year_from and birth_year_to:
        years = f"{birth_year_to}-{birth_year_from} г.р."
    elif birth_year_from:
        years = f"{birth_year_from} г.р. и старше"
    else:
        years = raw_text[:140]
    return f"{prefix} ({years})"[:255]


def build_manual_group_name(
    *,
    competition: Competition,
    gender_scope: str,
    birth_year_from: int | None,
    birth_year_to: int | None,
) -> str:
    anchor_date = competition.start_date or competition.date or timezone.localdate()
    season_start_year = infer_season_start_year(date_from=anchor_date, fallback_year=timezone.localdate().year)
    senior_birth_year_from = season_start_year - 16  # e.g. 2009 for season 2025/2026

    includes_senior = False
    if birth_year_to is not None:
        includes_senior = birth_year_to <= senior_birth_year_from
    elif birth_year_from is not None:
        includes_senior = birth_year_from <= senior_birth_year_from

    if includes_senior:
        prefix = "Женщины" if gender_scope == CompetitionScoringGroup.GenderScope.FEMALE else "Мужчины"
    else:
        prefix = "Девочки/девушки" if gender_scope == CompetitionScoringGroup.GenderScope.FEMALE else "Мальчики/юноши"
    if birth_year_from and birth_year_to:
        years = f"{birth_year_to}-{birth_year_from} г.р."
    elif birth_year_from:
        years = f"{birth_year_from} г.р. и старше"
    elif birth_year_to:
        years = f"до {birth_year_to} г.р."
    else:
        years = "возраст не указан"
    return f"{prefix} ({years})"[:255]


@dataclass
class ParsedGroupSpec:
    name: str
    gender_scope: str
    birth_year_from: int | None
    birth_year_to: int | None
    discipline: str
    source_raw_text: str
    parse_status: str
    parse_comment: str


def parse_age_categories(raw_lines: Iterable[str] | None) -> list[ParsedGroupSpec]:
    result: list[ParsedGroupSpec] = []
    for raw_line in raw_lines or []:
        if not isinstance(raw_line, str):
            continue
        line = raw_line.strip()
        if not line:
            continue

        has_male, has_female = _detect_gender_tokens(line)
        birth_year_from, birth_year_to = _extract_year_range(line)
        discipline = _extract_discipline(line)
        parse_status = CompetitionScoringGroup.ParseStatus.PARSED
        parse_comment = ""
        if birth_year_from is None and birth_year_to is None:
            parse_status = CompetitionScoringGroup.ParseStatus.PARTIAL
            parse_comment = "Годы рождения не распознаны, требуется проверка."

        genders: list[str] = []
        if has_male:
            genders.append(CompetitionScoringGroup.GenderScope.MALE)
        if has_female:
            genders.append(CompetitionScoringGroup.GenderScope.FEMALE)

        for gender_scope in genders:
            result.append(
                ParsedGroupSpec(
                    name=_group_title(
                        raw_text=line,
                        gender_scope=gender_scope,
                        birth_year_from=birth_year_from,
                        birth_year_to=birth_year_to,
                    ),
                    gender_scope=gender_scope,
                    birth_year_from=birth_year_from,
                    birth_year_to=birth_year_to,
                    discipline=discipline,
                    source_raw_text=line[:500],
                    parse_status=parse_status,
                    parse_comment=parse_comment[:500],
                )
            )
    return result


def _group_identity_key(spec: ParsedGroupSpec) -> tuple[str, int | None, int | None, str]:
    return (
        spec.gender_scope,
        spec.birth_year_from,
        spec.birth_year_to,
        _normalize_text(spec.discipline),
    )


def ensure_competition_scoring_groups_from_analysis(
    *,
    competition: Competition,
    analysis: DocumentAIAnalysis | None,
    source_document: Document | None = None,
    only_if_empty: bool = False,
) -> int:
    if not analysis or not analysis.is_analyzed_successfully:
        return 0
    if analysis.doc_type != DocumentAIAnalysis.DocType.COMPETITION_GENERAL:
        return 0
    if only_if_empty and competition.scoring_groups.filter(is_active=True).exists():
        return 0

    extracted = analysis.extracted or {}
    requirements = extracted.get("requirements") or {}
    age_categories = requirements.get("age_categories") if isinstance(requirements, dict) else None
    specs = parse_age_categories(age_categories)
    if not specs:
        return 0

    existing_groups = list(competition.scoring_groups.filter(is_active=True))
    existing_keys = {
        (
            group.gender_scope,
            group.birth_year_from,
            group.birth_year_to,
            _normalize_text(group.discipline),
        )
        for group in existing_groups
    }

    created_count = 0
    max_sort_order = (
        competition.scoring_groups.order_by("-sort_order").values_list("sort_order", flat=True).first() or 0
    )
    for spec in specs:
        resolved_discipline = (spec.discipline or normalize_discipline_value(competition.discipline) or "")[:100]
        key = _group_identity_key(spec)
        if not spec.discipline:
            key = (
                spec.gender_scope,
                spec.birth_year_from,
                spec.birth_year_to,
                _normalize_text(resolved_discipline),
            )
        if key in existing_keys:
            continue
        max_sort_order += 1
        CompetitionScoringGroup.objects.create(
            competition=competition,
            name=spec.name,
            gender_scope=spec.gender_scope,
            birth_year_from=spec.birth_year_from,
            birth_year_to=spec.birth_year_to,
            discipline=resolved_discipline,
            source=CompetitionScoringGroup.SourceType.AI,
            source_document=source_document,
            source_raw_text=spec.source_raw_text,
            parse_status=spec.parse_status,
            parse_comment=spec.parse_comment,
            sort_order=max_sort_order,
            is_active=True,
        )
        existing_keys.add(key)
        created_count += 1
    return created_count


def ensure_competition_scoring_groups_from_competition_documents(
    *,
    competition: Competition,
    only_if_empty: bool = True,
) -> int:
    if only_if_empty and competition.scoring_groups.filter(is_active=True).exists():
        return 0

    created_total = 0
    links = (
        CompetitionDocument.objects.select_related("document__ai_analysis")
        .filter(competition=competition)
        .order_by("-created_at")
    )
    for link in links:
        analysis = getattr(link.document, "ai_analysis", None)
        created_total += ensure_competition_scoring_groups_from_analysis(
            competition=competition,
            analysis=analysis,
            source_document=link.document,
            only_if_empty=only_if_empty,
        )
        if only_if_empty and created_total > 0:
            break
    return created_total


def ensure_standard_u_category_groups(*, competition: Competition, category_code: str) -> int:
    category = STANDARD_U_CATEGORIES.get(category_code.upper())
    if not category:
        return 0

    anchor_date = competition.start_date or competition.date or timezone.localdate()
    season_start_year = infer_season_start_year(date_from=anchor_date, fallback_year=timezone.localdate().year)

    birth_year_from = season_start_year - category.age_min
    birth_year_to = (season_start_year - category.age_max) if category.age_max is not None else None
    discipline = normalize_discipline_value(competition.discipline)
    existing_keys = {
        (
            group.gender_scope,
            group.birth_year_from,
            group.birth_year_to,
            _normalize_text(group.discipline),
        )
        for group in competition.scoring_groups.filter(is_active=True)
    }

    created = 0
    next_order = competition.scoring_groups.order_by("-sort_order").values_list("sort_order", flat=True).first() or 0
    for gender_scope, label in (
        (CompetitionScoringGroup.GenderScope.FEMALE, category.female_label),
        (CompetitionScoringGroup.GenderScope.MALE, category.male_label),
    ):
        key = (gender_scope, birth_year_from, birth_year_to, _normalize_text(discipline))
        if key in existing_keys:
            # Backfill marker for legacy rows created before standard_category field existed.
            CompetitionScoringGroup.objects.filter(
                competition=competition,
                is_active=True,
                gender_scope=gender_scope,
                birth_year_from=birth_year_from,
                birth_year_to=birth_year_to,
            ).filter(standard_category="").update(standard_category=category.code)
            continue
        next_order += 1
        CompetitionScoringGroup.objects.create(
            competition=competition,
            name=(
                f"{label} ({birth_year_to}-{birth_year_from} г.р., {category.button_label})"
                if birth_year_to is not None
                else f"{label} ({birth_year_from} г.р. и старше, {category.button_label})"
            ),
            gender_scope=gender_scope,
            birth_year_from=birth_year_from,
            birth_year_to=birth_year_to,
            discipline=discipline,
            standard_category=category.code,
            source=CompetitionScoringGroup.SourceType.MANUAL,
            parse_status=CompetitionScoringGroup.ParseStatus.PARSED,
            parse_comment=f"Автосоздано по стандартной категории {category.button_label}",
            sort_order=next_order,
            is_active=True,
        )
        existing_keys.add(key)
        created += 1
    return created


def detect_standard_u_category_code_for_birth_year(*, competition: Competition, birth_year: int | None) -> str | None:
    if not birth_year:
        return None
    anchor_date = competition.start_date or competition.date or timezone.localdate()
    season_start_year = infer_season_start_year(date_from=anchor_date, fallback_year=timezone.localdate().year)
    for code, category in STANDARD_U_CATEGORIES.items():
        birth_year_from = season_start_year - category.age_min
        birth_year_to = (season_start_year - category.age_max) if category.age_max is not None else None
        if birth_year_to is None:
            if birth_year <= birth_year_from:
                return code
            continue
        if birth_year_to <= birth_year <= birth_year_from:
            return code
    return None


def ensure_standard_u_groups_for_birth_year(*, competition: Competition, birth_year: int | None) -> int:
    category_code = detect_standard_u_category_code_for_birth_year(competition=competition, birth_year=birth_year)
    if not category_code:
        return 0
    return ensure_standard_u_category_groups(competition=competition, category_code=category_code)
