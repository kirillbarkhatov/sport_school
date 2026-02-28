from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from difflib import SequenceMatcher
from typing import Any

from django.utils import timezone

from .models import Athlete, AthleteDocument, Competition, CompetitionDocument, Document, DocumentAIAnalysis, DocumentType, Person


_COMPETITION_DOC_TYPES = {
    DocumentType.REGULATION,
    DocumentType.SCHEDULE,
    DocumentType.START_LIST,
    DocumentType.START_LIST_SECOND,
    DocumentType.INTERMEDIATE_RESULTS,
    DocumentType.PRELIM_RESULTS,
    DocumentType.OFFICIAL_RESULTS,
    DocumentType.APPLICATION_FORM,
    DocumentType.OTHER,
}

_ATHLETE_DOC_TYPES = {
    DocumentType.PARENT_CONSENT,
    DocumentType.MED_CERT,
    DocumentType.INSURANCE,
    DocumentType.PASSPORT,
    DocumentType.BIRTH_CERTIFICATE,
    DocumentType.RANK_BOOK,
    DocumentType.RUSADA_CERTIFICATE,
    DocumentType.OTHER,
}


@dataclass
class AutoBindResult:
    bound: bool
    entity_type: str | None = None
    entity_id: int | None = None
    doc_type: str | None = None


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = value.lower().strip().replace("ё", "е")
    normalized = re.sub(r"[^\w\dа-яё]+", "", normalized, flags=re.IGNORECASE)
    return normalized


def _safe_date(raw: Any) -> date | None:
    if not raw:
        return None
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            yyyy, mm, dd = raw.split("-")
            return date(int(yyyy), int(mm), int(dd))
        except Exception:
            return None
    return None


def _analysis_dates(analysis: DocumentAIAnalysis | None) -> tuple[date | None, date | None]:
    if not analysis:
        return None, None
    extracted = analysis.extracted or {}
    return _safe_date(extracted.get("issue_date")), _safe_date(extracted.get("valid_until"))


def _sync_med_cert_actual_flags(athlete: Athlete) -> None:
    today = timezone.localdate()
    med_docs = list(
        AthleteDocument.objects.filter(athlete=athlete, doc_type=DocumentType.MED_CERT).order_by("-created_at")
    )
    if not med_docs:
        return

    for doc in med_docs:
        if doc.valid_until and doc.valid_until < today and doc.is_actual:
            doc.is_actual = False
            doc.save(update_fields=["is_actual"])

    valid_docs = [
        doc for doc in med_docs
        if doc.valid_until and doc.valid_until >= today
    ]
    if valid_docs:
        active_id = valid_docs[0].id
    else:
        pending_docs = [doc for doc in med_docs if doc.needs_valid_until_clarification]
        active_id = pending_docs[0].id if pending_docs else None
    for doc in med_docs:
        should_be_actual = active_id is not None and doc.id == active_id
        if doc.is_actual != should_be_actual:
            doc.is_actual = should_be_actual
            doc.save(update_fields=["is_actual"])


def sync_athlete_document_from_analysis(document: Document) -> None:
    analysis = getattr(document, "ai_analysis", None)
    if not analysis or not analysis.is_analyzed_successfully:
        return

    issued_at, valid_until = _analysis_dates(analysis)
    links = AthleteDocument.objects.select_related("athlete").filter(document=document)
    if not links.exists():
        return

    updated_athlete_ids: set[int] = set()
    for link in links:
        update_fields: list[str] = []
        if issued_at and link.issued_at != issued_at:
            link.issued_at = issued_at
            update_fields.append("issued_at")
        if valid_until and link.valid_until != valid_until:
            link.valid_until = valid_until
            update_fields.append("valid_until")
        needs_clarification = valid_until is None and link.doc_type == DocumentType.MED_CERT
        if link.needs_valid_until_clarification != needs_clarification:
            link.needs_valid_until_clarification = needs_clarification
            update_fields.append("needs_valid_until_clarification")

        if update_fields:
            link.save(update_fields=update_fields)
        updated_athlete_ids.add(link.athlete_id)

    for athlete_id in updated_athlete_ids:
        athlete = Athlete.objects.filter(pk=athlete_id).first()
        if athlete:
            _sync_med_cert_actual_flags(athlete)


def _build_full_name(person_block: dict[str, Any]) -> str:
    if not isinstance(person_block, dict):
        return ""
    full_name = person_block.get("full_name")
    if isinstance(full_name, str) and full_name.strip():
        return full_name.strip()
    parts = [
        person_block.get("last_name"),
        person_block.get("first_name"),
        person_block.get("middle_name"),
    ]
    return " ".join(str(part).strip() for part in parts if part).strip()


def _extract_name_parts_from_analysis(analysis: DocumentAIAnalysis) -> tuple[str, str, str]:
    extracted = analysis.extracted or {}
    person_block = extracted.get("person") or {}
    if not isinstance(person_block, dict):
        return "", "", ""

    last_name = normalize_text(person_block.get("last_name"))
    first_name = normalize_text(person_block.get("first_name"))
    middle_name = normalize_text(person_block.get("middle_name"))

    if last_name and first_name:
        return last_name, first_name, middle_name

    full_name = _build_full_name(person_block)
    if full_name:
        parts = [normalize_text(part) for part in full_name.split() if part]
        if len(parts) >= 2:
            ln = last_name or parts[0]
            fn = first_name or parts[1]
            mn = middle_name or (parts[2] if len(parts) >= 3 else "")
            return ln, fn, mn

    return last_name, first_name, middle_name


def _athlete_name_parts(athlete: Athlete) -> tuple[str, str, str]:
    person = athlete.person
    return (
        normalize_text(person.surname),
        normalize_text(person.name),
        normalize_text(person.middlename),
    )


def _collect_athlete_candidates_by_name(
    analysis: DocumentAIAnalysis,
) -> dict[str, list[Athlete]]:
    last_name, first_name, middle_name = _extract_name_parts_from_analysis(analysis)
    athletes = list(Athlete.objects.select_related("person").all())

    fio_candidates: list[Athlete] = []
    fi_candidates: list[Athlete] = []
    surname_candidates: list[Athlete] = []

    for athlete in athletes:
        ln, fn, mn = _athlete_name_parts(athlete)
        if last_name and first_name and middle_name and ln == last_name and fn == first_name and mn == middle_name:
            fio_candidates.append(athlete)
            continue
        if last_name and first_name and ln == last_name and fn == first_name:
            fi_candidates.append(athlete)
            continue
        if last_name and ln == last_name:
            surname_candidates.append(athlete)

    return {
        "fio": fio_candidates,
        "fi": fi_candidates,
        "surname": surname_candidates,
    }


def _resolve_unique_with_birth_date(cands: list[Athlete], birth_date: date | None) -> Athlete | None:
    if len(cands) == 1:
        return cands[0]
    if birth_date and cands:
        by_dob = [a for a in cands if a.person.date_of_birth == birth_date]
        if len(by_dob) == 1:
            return by_dob[0]
    return None


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _find_athlete_fuzzy_match(
    *,
    last_name: str,
    first_name: str,
    middle_name: str,
    birth_date: date | None,
) -> Athlete | None:
    # Fuzzy auto-bind is allowed only when surname+name are present.
    if not last_name or not first_name:
        return None

    athletes = list(Athlete.objects.select_related("person").all())
    target_fio = f"{last_name}{first_name}{middle_name}".strip()
    target_fi = f"{last_name}{first_name}".strip()

    # 1) Fuzzy by ФИО first.
    if middle_name and len(target_fio) >= 8:
        fuzzy_fio_candidates: list[Athlete] = []
        for athlete in athletes:
            ln, fn, mn = _athlete_name_parts(athlete)
            candidate_fio = f"{ln}{fn}{mn}".strip()
            if _similarity(target_fio, candidate_fio) >= 0.90:
                fuzzy_fio_candidates.append(athlete)
        resolved = _resolve_unique_with_birth_date(fuzzy_fio_candidates, birth_date)
        if resolved:
            return resolved

    # 2) Then fuzzy by ФИ.
    if len(target_fi) >= 6:
        fuzzy_fi_candidates: list[Athlete] = []
        for athlete in athletes:
            ln, fn, _ = _athlete_name_parts(athlete)
            candidate_fi = f"{ln}{fn}".strip()
            if _similarity(target_fi, candidate_fi) >= 0.90:
                fuzzy_fi_candidates.append(athlete)
        resolved = _resolve_unique_with_birth_date(fuzzy_fi_candidates, birth_date)
        if resolved:
            return resolved

    return None


def infer_competition_doc_type(analysis: DocumentAIAnalysis) -> str:
    extracted = analysis.extracted or {}
    corpus = " ".join(
        str(v)
        for v in [analysis.title, extracted.get("competition_name"), extracted.get("summary"), extracted.get("discipline")]
        if v
    ).lower()

    mapping = [
        (DocumentType.SCHEDULE, ["распис", "schedule", "тайминг", "тайминг"]),
        (DocumentType.START_LIST_SECOND, ["второй", "2 попыт", "second"]),
        (DocumentType.START_LIST, ["стартов", "start list", "лист"]),
        (DocumentType.INTERMEDIATE_RESULTS, ["промежуточ", "intermediate"]),
        (DocumentType.PRELIM_RESULTS, ["предварит", "prelim"]),
        (DocumentType.OFFICIAL_RESULTS, ["официаль", "final", "результат"]),
        (DocumentType.APPLICATION_FORM, ["заявк", "application"]),
        (DocumentType.REGULATION, ["регламент", "положени", "regulation"]),
    ]
    for doc_type, keywords in mapping:
        if any(keyword in corpus for keyword in keywords):
            return doc_type
    return DocumentType.OTHER


def infer_athlete_doc_type(analysis: DocumentAIAnalysis) -> str:
    extracted = analysis.extracted or {}
    corpus = " ".join(
        str(v)
        for v in [analysis.title, extracted.get("document_name"), extracted.get("summary")]
        if v
    ).lower()

    mapping = [
        (DocumentType.PARENT_CONSENT, ["соглас", "consent"]),
        (DocumentType.MED_CERT, ["мед", "справк", "medical"]),
        (DocumentType.INSURANCE, ["страх", "insurance", "полис"]),
        (DocumentType.PASSPORT, ["паспорт", "passport"]),
        (DocumentType.BIRTH_CERTIFICATE, ["свидетельств", "рожд", "birth certificate"]),
        (DocumentType.RANK_BOOK, ["разряд", "книжк", "rank"]),
        (DocumentType.RUSADA_CERTIFICATE, ["русад", "rusada"]),
    ]
    for doc_type, keywords in mapping:
        if any(keyword in corpus for keyword in keywords):
            return doc_type
    return DocumentType.OTHER


def find_competition_exact_match(analysis: DocumentAIAnalysis) -> Competition | None:
    extracted = analysis.extracted or {}
    name = extracted.get("competition_name")
    event_dates = extracted.get("event_dates") or {}
    date_from = _safe_date(event_dates.get("from"))
    date_to = _safe_date(event_dates.get("to"))

    if not name:
        return None

    target_name = normalize_text(str(name))
    candidates = Competition.objects.all().only("id", "name", "date", "start_date", "end_date")

    matches = []
    for comp in candidates:
        if normalize_text(comp.name) != target_name:
            continue

        comp_from = comp.start_date or comp.date
        comp_to = comp.end_date or comp.date

        same_from = (date_from is None) or (comp_from == date_from)
        same_to = (date_to is None) or (comp_to == date_to)
        if same_from and same_to:
            matches.append(comp)

    if len(matches) == 1:
        return matches[0]
    return None


def _athlete_matches_by_name(full_name: str) -> list[Athlete]:
    target = normalize_text(full_name)
    if not target:
        return []

    result: list[Athlete] = []
    for athlete in Athlete.objects.select_related("person").all():
        person = athlete.person
        candidate = " ".join(
            part for part in [person.surname or "", person.name or "", person.middlename or ""] if part
        )
        if normalize_text(candidate) == target:
            result.append(athlete)
    return result


def find_athlete_exact_match(analysis: DocumentAIAnalysis) -> Athlete | None:
    extracted = analysis.extracted or {}
    person_block = extracted.get("person") or {}
    birth_date = _safe_date(person_block.get("birth_date"))

    def _pick_unique(candidates: list[Athlete]) -> Athlete | None:
        if len(candidates) == 1:
            return candidates[0]
        if birth_date and candidates:
            filtered = [a for a in candidates if a.person.date_of_birth == birth_date]
            if len(filtered) == 1:
                return filtered[0]
        return None

    candidates = _collect_athlete_candidates_by_name(analysis)
    fio = _pick_unique(candidates["fio"])
    if fio:
        return fio
    fi = _pick_unique(candidates["fi"])
    if fi:
        return fi
    surname = _pick_unique(candidates["surname"])
    if surname:
        return surname
    return None


def find_athlete_auto_bind_match(analysis: DocumentAIAnalysis) -> Athlete | None:
    """
    Strict auto-bind matcher (requested behavior):
    1) full name (ФИО) unique -> match
    2) last+first (ФИ) unique -> match
    3) if multiple on either level, disambiguate by birth_date
    4) otherwise no auto-bind
    """
    extracted = analysis.extracted or {}
    person_block = extracted.get("person") or {}
    birth_date = _safe_date(person_block.get("birth_date"))
    candidates = _collect_athlete_candidates_by_name(analysis)

    def _resolve(cands: list[Athlete]) -> Athlete | None:
        return _resolve_unique_with_birth_date(cands, birth_date)

    fio = _resolve(candidates["fio"])
    if fio:
        return fio

    fi = _resolve(candidates["fi"])
    if fi:
        return fi

    # Fallback: fuzzy by ФИО -> ФИ (never by birth date alone).
    last_name, first_name, middle_name = _extract_name_parts_from_analysis(analysis)
    fuzzy = _find_athlete_fuzzy_match(
        last_name=last_name,
        first_name=first_name,
        middle_name=middle_name,
        birth_date=birth_date,
    )
    if fuzzy:
        return fuzzy

    return None


def auto_bind_document_by_analysis(document: Document) -> AutoBindResult:
    analysis = DocumentAIAnalysis.objects.filter(document_id=document.id).first()
    if not analysis or not analysis.is_analyzed_successfully:
        return AutoBindResult(bound=False)

    if (
        CompetitionDocument.objects.filter(document_id=document.id).exists()
        or AthleteDocument.objects.filter(document_id=document.id).exists()
    ):
        return AutoBindResult(bound=False)

    if analysis.doc_type == DocumentAIAnalysis.DocType.COMPETITION_GENERAL:
        competition = find_competition_exact_match(analysis)
        if not competition:
            return AutoBindResult(bound=False)

        doc_type = infer_competition_doc_type(analysis)
        if doc_type not in _COMPETITION_DOC_TYPES:
            doc_type = DocumentType.OTHER

        CompetitionDocument.objects.get_or_create(
            competition=competition,
            document=document,
            defaults={
                "doc_type": doc_type,
                "title": analysis.title or "",
                "is_public": False,
            },
        )
        return AutoBindResult(bound=True, entity_type="competition", entity_id=competition.id, doc_type=doc_type)

    if analysis.doc_type == DocumentAIAnalysis.DocType.ATHLETE_SPECIFIC:
        athlete = find_athlete_auto_bind_match(analysis)
        if not athlete:
            return AutoBindResult(bound=False)

        doc_type = infer_athlete_doc_type(analysis)
        if doc_type not in _ATHLETE_DOC_TYPES:
            doc_type = DocumentType.OTHER

        AthleteDocument.objects.get_or_create(
            athlete=athlete,
            document=document,
            defaults={
                "doc_type": doc_type,
            },
        )
        sync_athlete_document_from_analysis(document)
        return AutoBindResult(bound=True, entity_type="athlete", entity_id=athlete.id, doc_type=doc_type)

    return AutoBindResult(bound=False)


def get_binding_context(document: Document) -> dict[str, Any]:
    analysis = getattr(document, "ai_analysis", None)

    competitions = list(
        Competition.objects.order_by("-start_date", "-date", "name").values(
            "id", "name", "date", "start_date", "end_date"
        )
    )
    athletes = list(
        Athlete.objects.select_related("person")
        .order_by("person__surname", "person__name")
        .values(
            "id", "person__surname", "person__name", "person__middlename", "person__date_of_birth"
        )
    )

    result: dict[str, Any] = {
        "document_id": document.id,
        "has_analysis": bool(analysis),
        "analysis_status": analysis.status if analysis else None,
        "analysis_doc_type": analysis.doc_type if analysis else None,
        "analysis_extracted": analysis.extracted if analysis else {},
        "analysis_title": analysis.title if analysis else None,
        "competition_doc_types": [
            {"value": value, "label": label}
            for value, label in DocumentType.choices
            if value in _COMPETITION_DOC_TYPES
        ],
        "athlete_doc_types": [
            {"value": value, "label": label}
            for value, label in DocumentType.choices
            if value in _ATHLETE_DOC_TYPES
        ],
        "competitions": competitions,
        "athletes": [
            {
                "id": item["id"],
                "full_name": " ".join(
                    part for part in [item["person__surname"], item["person__name"], item["person__middlename"]] if part
                ),
                "birth_date": item["person__date_of_birth"],
            }
            for item in athletes
        ],
        "suggested": {
            "competition": None,
            "athlete": None,
            "match_level": None,
            "by_name_athletes": [],
            "by_birth_date_athletes": [],
            "suggested_doc_type": None,
        },
    }

    if not analysis:
        return result

    if analysis.doc_type == DocumentAIAnalysis.DocType.COMPETITION_GENERAL:
        match = find_competition_exact_match(analysis)
        if match:
            result["suggested"]["competition"] = {
                "id": match.id,
                "name": match.name,
                "date": match.date,
                "start_date": match.start_date,
                "end_date": match.end_date,
            }
        result["suggested"]["suggested_doc_type"] = infer_competition_doc_type(analysis)

    elif analysis.doc_type == DocumentAIAnalysis.DocType.ATHLETE_SPECIFIC:
        auto_match = find_athlete_auto_bind_match(analysis)
        match = auto_match or find_athlete_exact_match(analysis)
        extracted = analysis.extracted or {}
        person_block = extracted.get("person") or {}
        birth_date = _safe_date(person_block.get("birth_date"))
        candidates = _collect_athlete_candidates_by_name(analysis)

        if match:
            match_level = None
            if any(a.id == match.id for a in candidates["fio"]):
                match_level = "fio"
            elif any(a.id == match.id for a in candidates["fi"]):
                match_level = "fi"
            elif any(a.id == match.id for a in candidates["surname"]):
                match_level = "surname"
            result["suggested"]["athlete"] = {
                "id": match.id,
                "full_name": f"{match.person.surname} {match.person.name} {match.person.middlename or ''}".strip(),
                "birth_date": match.person.date_of_birth,
            }
            result["suggested"]["match_level"] = match_level
        else:
            by_name_candidates: list[dict[str, Any]] = []
            match_level = None
            if candidates["fio"]:
                by_name_candidates = candidates["fio"][:20]
                match_level = "fio"
            elif candidates["fi"]:
                by_name_candidates = candidates["fi"][:20]
                match_level = "fi"
            elif candidates["surname"]:
                by_name_candidates = candidates["surname"][:20]
                match_level = "surname"

            result["suggested"]["match_level"] = match_level
            result["suggested"]["by_name_athletes"] = [
                {
                    "id": athlete.id,
                    "full_name": f"{athlete.person.surname} {athlete.person.name} {athlete.person.middlename or ''}".strip(),
                    "birth_date": athlete.person.date_of_birth,
                }
                for athlete in by_name_candidates
            ]

        if (not match) and (not result["suggested"]["by_name_athletes"]) and birth_date:
            by_dob = Athlete.objects.select_related("person").filter(person__date_of_birth=birth_date)[:20]
            result["suggested"]["by_birth_date_athletes"] = [
                {
                    "id": athlete.id,
                    "full_name": f"{athlete.person.surname} {athlete.person.name} {athlete.person.middlename or ''}".strip(),
                    "birth_date": athlete.person.date_of_birth,
                }
                for athlete in by_dob
            ]

        result["suggested"]["suggested_doc_type"] = infer_athlete_doc_type(analysis)

    return result


def bind_document_to_competition(*, document: Document, competition: Competition, doc_type: str, title: str = "") -> CompetitionDocument:
    return CompetitionDocument.objects.create(
        competition=competition,
        document=document,
        doc_type=doc_type,
        title=title,
        is_public=False,
    )


def bind_document_to_athlete(*, document: Document, athlete: Athlete, doc_type: str) -> AthleteDocument:
    analysis = getattr(document, "ai_analysis", None)
    issued_at, valid_until = _analysis_dates(analysis)
    link = AthleteDocument.objects.create(
        athlete=athlete,
        document=document,
        doc_type=doc_type,
        issued_at=issued_at,
        valid_until=valid_until,
        needs_valid_until_clarification=bool(doc_type == DocumentType.MED_CERT and valid_until is None),
    )
    if doc_type == DocumentType.MED_CERT:
        _sync_med_cert_actual_flags(athlete)
    return link


def create_competition_from_analysis(document: Document) -> Competition:
    analysis = document.ai_analysis
    extracted = analysis.extracted or {}
    event_dates = extracted.get("event_dates") or {}

    competition = Competition.objects.create(
        name=(extracted.get("competition_name") or analysis.title or document.original_name)[:100],
        start_date=_safe_date(event_dates.get("from")),
        end_date=_safe_date(event_dates.get("to")),
        date=_safe_date(event_dates.get("from")) or _safe_date(event_dates.get("to")),
        location=(extracted.get("location") or "Не указано")[:100],
        discipline=(extracted.get("discipline") or "")[:100] or None,
        description=(extracted.get("summary") or analysis.title or "")[:1000] or None,
    )
    return competition


def create_athlete_from_analysis(*, document: Document, gender: str) -> Athlete:
    analysis = document.ai_analysis
    extracted = analysis.extracted or {}
    person_block = extracted.get("person") or {}

    last_name = (person_block.get("last_name") or "")[:100]
    first_name = (person_block.get("first_name") or "")[:100]
    middle_name = (person_block.get("middle_name") or "")[:100] or None

    full_name = _build_full_name(person_block)
    if (not last_name or not first_name) and full_name:
        parts = full_name.split()
        if len(parts) >= 2:
            last_name = last_name or parts[0][:100]
            first_name = first_name or parts[1][:100]
            if len(parts) >= 3:
                middle_name = middle_name or parts[2][:100]

    if not last_name:
        last_name = "Неизвестно"
    if not first_name:
        first_name = "Неизвестно"

    person = Person.objects.create(
        surname=last_name,
        name=first_name,
        middlename=middle_name,
        date_of_birth=_safe_date(person_block.get("birth_date")),
        gender=gender,
    )
    athlete = Athlete.objects.create(
        person=person,
        level="unknown",
    )
    return athlete
