from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from typing import Iterable

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from members.models import (
    PersonDedupJob,
    PersonDuplicateCluster,
    PersonDuplicateItem,
    PersonMergeLog,
    PersonMergeRedirect,
)
from school.models import (
    Athlete,
    AthleteDocument,
    CampEnrollment,
    ClassEnrollment,
    CompetitionEntry,
    Family,
    FamilyAthleteProfile,
    FamilyMember,
    Person,
    PotentialClient,
    Coach,
)
from users.models import User, UserAthleteLink, UserPersonLink

NON_LETTER_RE = re.compile(r"[^a-zа-яё]")
PHONE_CLEAN_RE = re.compile(r"\D+")
TELEGRAM_RE = re.compile(r"(?:https?://t\.me/)?@?(?P<value>[\w+\d_]+)", re.IGNORECASE)

PERSON_FIELDS = (
    "surname",
    "name",
    "middlename",
    "date_of_birth",
    "phone",
    "email",
    "telegram",
    "club",
    "gender",
    "comment",
)


def _normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(str(value).strip().lower().replace("ё", "е").split())


def _normalize_name(value: str | None) -> str:
    normalized = _normalize_text(value)
    return NON_LETTER_RE.sub("", normalized)


def _normalize_phone(value: str | None) -> str:
    if not value:
        return ""
    digits = PHONE_CLEAN_RE.sub("", str(value))
    if not digits:
        return ""
    if len(digits) == 10:
        digits = "7" + digits
    elif digits.startswith("8") and len(digits) == 11:
        digits = "7" + digits[1:]
    if len(digits) > 11:
        digits = digits[-11:]
    return digits


def _normalize_email(value: str | None) -> str:
    return _normalize_text(value)


def _normalize_telegram(value: str | None) -> str:
    raw = _normalize_text(value)
    if not raw:
        return ""
    match = TELEGRAM_RE.search(raw)
    return match.group("value").lower() if match else raw.lstrip("@")


def _is_redirected(person_id: int) -> bool:
    return PersonMergeRedirect.objects.filter(source_person_id=person_id, is_active=True).exists()


def _canonical_person(person: Person) -> Person:
    redirect = PersonMergeRedirect.objects.filter(source_person=person, is_active=True).select_related("target_person").first()
    return redirect.target_person if redirect else person


def _person_completeness(person: Person) -> int:
    score = 0
    for field in ("surname", "name", "middlename", "date_of_birth", "phone", "email", "telegram", "club"):
        value = getattr(person, field, None)
        if value:
            score += 1
    if Athlete.objects.filter(person=person).exists():
        score += 3
    score += User.objects.filter(person=person).count() * 2
    score += FamilyMember.objects.filter(person=person).count()
    return score


@dataclass
class PairScore:
    left_id: int
    right_id: int
    score: int
    reasons: list[str]


def _score_pair(left: Person, right: Person) -> PairScore:
    score = 0
    reasons: list[str] = []

    surname_left = _normalize_name(left.surname)
    surname_right = _normalize_name(right.surname)
    if surname_left and surname_left == surname_right:
        score += 35
        reasons.append("surname_exact")

    name_left = _normalize_name(left.name)
    name_right = _normalize_name(right.name)
    if name_left and name_left == name_right:
        score += 25
        reasons.append("name_exact")

    middlename_left = _normalize_name(left.middlename)
    middlename_right = _normalize_name(right.middlename)
    if middlename_left and middlename_left == middlename_right:
        score += 15
        reasons.append("middlename_exact")

    if left.date_of_birth and right.date_of_birth:
        if left.date_of_birth == right.date_of_birth:
            score += 25
            reasons.append("dob_exact")
        elif left.date_of_birth.year == right.date_of_birth.year:
            score += 10
            reasons.append("dob_year")

    phone_left = _normalize_phone(left.phone)
    phone_right = _normalize_phone(right.phone)
    if phone_left and phone_left == phone_right:
        score += 30
        reasons.append("phone_exact")

    email_left = _normalize_email(left.email)
    email_right = _normalize_email(right.email)
    if email_left and email_left == email_right:
        score += 25
        reasons.append("email_exact")

    tg_left = _normalize_telegram(left.telegram)
    tg_right = _normalize_telegram(right.telegram)
    if tg_left and tg_left == tg_right:
        score += 30
        reasons.append("telegram_exact")

    if left.gender and right.gender and left.gender != right.gender:
        score -= 20
        reasons.append("gender_conflict")

    return PairScore(left_id=left.id, right_id=right.id, score=score, reasons=reasons)


class _UnionFind:
    def __init__(self, ids: Iterable[int]):
        self.parent = {value: value for value in ids}

    def find(self, value: int) -> int:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: int, right: int):
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _build_blocks(persons: list[Person]) -> dict[str, list[Person]]:
    blocks: dict[str, list[Person]] = defaultdict(list)
    for person in persons:
        surname = _normalize_name(person.surname)
        name = _normalize_name(person.name)
        if not surname or not name:
            continue
        year = str(person.date_of_birth.year) if person.date_of_birth else "0000"
        gender = person.gender or ""

        # Main key: stricter, keeps pair count reasonable on larger datasets.
        primary_key = f"strict:{surname[:4]}:{name[:2]}:{year}:{gender}"
        blocks[primary_key].append(person)

        # Soft key: allows matches when birth year is empty/incorrect.
        soft_year_key = f"soft_year:{surname[:4]}:{name[:2]}:{gender}"
        blocks[soft_year_key].append(person)

        # Softest key: catches gender mistakes in source data.
        soft_gender_key = f"soft_gender:{surname[:4]}:{name[:2]}"
        blocks[soft_gender_key].append(person)
    return blocks


def _suggest_master_person(people: list[Person]) -> Person:
    return sorted(people, key=lambda person: (_person_completeness(person), person.id), reverse=True)[0]


def _get_safe_unique(values: list[str]) -> str | None:
    cleaned = [value for value in values if value]
    uniq = sorted(set(cleaned))
    if len(uniq) == 1:
        return uniq[0]
    return None


def _is_safe_auto_merge(people: list[Person], cluster_score: int, auto_merge_score: int) -> bool:
    if len(people) < 2 or cluster_score < auto_merge_score:
        return False

    surname = _get_safe_unique([_normalize_name(p.surname) for p in people])
    name = _get_safe_unique([_normalize_name(p.name) for p in people])
    if not surname or not name:
        return False

    genders = set(p.gender for p in people if p.gender)
    if len(genders) > 1:
        return False

    dob_values = set(p.date_of_birth for p in people if p.date_of_birth)
    if len(dob_values) > 1:
        return False

    normalizers = (
        ("phone", _normalize_phone),
        ("email", _normalize_email),
        ("telegram", _normalize_telegram),
    )
    for field_name, normalizer in normalizers:
        values = [normalizer(getattr(p, field_name)) for p in people]
        uniq = {value for value in values if value}
        if len(uniq) > 1:
            return False

    return True


def _pick_field_value(persons: list[Person], field_name: str):
    for person in sorted(persons, key=lambda p: (_person_completeness(p), p.id), reverse=True):
        value = getattr(person, field_name)
        if value:
            return value, person.id
    return getattr(persons[0], field_name), persons[0].id


def _reassign_athlete_refs(source_athlete: Athlete, target_athlete: Athlete) -> dict[str, int]:
    moved = {}
    moved["class_enrollments"] = ClassEnrollment.objects.filter(athlete=source_athlete).update(athlete=target_athlete)
    moved["camp_enrollments"] = CampEnrollment.objects.filter(athlete=source_athlete).update(athlete=target_athlete)
    moved["competition_entries"] = CompetitionEntry.objects.filter(athlete=source_athlete).update(athlete=target_athlete)
    moved["athlete_documents"] = AthleteDocument.objects.filter(athlete=source_athlete).update(athlete=target_athlete)

    profile_moved = 0
    for profile in FamilyAthleteProfile.objects.filter(athlete=source_athlete):
        existing = FamilyAthleteProfile.objects.filter(family=profile.family, athlete=target_athlete).first()
        if existing:
            profile.delete()
        else:
            profile.athlete = target_athlete
            profile.save(update_fields=["athlete"])
            profile_moved += 1
    moved["family_athlete_profiles"] = profile_moved

    link_moved = 0
    for link in UserAthleteLink.objects.filter(athlete=source_athlete):
        _, created = UserAthleteLink.objects.get_or_create(
            user=link.user,
            athlete=target_athlete,
            defaults={"source": link.source},
        )
        link.delete()
        if created:
            link_moved += 1
    moved["user_athlete_links"] = link_moved
    return moved


def _merge_athlete(master: Person, duplicate: Person) -> dict[str, int]:
    moved: dict[str, int] = {}
    master_athlete = Athlete.objects.filter(person=master).first()
    duplicate_athlete = Athlete.objects.filter(person=duplicate).first()

    if not duplicate_athlete:
        return moved

    if not master_athlete:
        duplicate_athlete.person = master
        duplicate_athlete.save(update_fields=["person"])
        moved["athlete_rebound"] = 1
        return moved

    nested = _reassign_athlete_refs(duplicate_athlete, master_athlete)
    moved.update(nested)
    duplicate_athlete.delete()
    moved["athlete_deleted"] = 1
    return moved


@transaction.atomic
def apply_cluster_merge(
    cluster: PersonDuplicateCluster,
    master_person_id: int,
    *,
    actor=None,
    field_resolution: dict | None = None,
    note: str = "",
    auto: bool = False,
) -> PersonMergeLog:
    cluster = PersonDuplicateCluster.objects.select_for_update().get(pk=cluster.pk)
    items = list(cluster.items.select_related("person").order_by("-aggregate_score"))
    people = [item.person for item in items]
    person_ids = {person.id for person in people}
    if master_person_id not in person_ids:
        raise ValueError("master person is not inside cluster")

    master = next(person for person in people if person.id == master_person_id)
    duplicates = [person for person in people if person.id != master_person_id]
    field_resolution = field_resolution or {}
    moved_relations: dict[str, int] = defaultdict(int)

    for field_name in PERSON_FIELDS:
        selected_person_id = field_resolution.get(field_name)
        if selected_person_id:
            selected = next((p for p in people if p.id == int(selected_person_id)), None)
            if selected is not None and getattr(selected, field_name):
                setattr(master, field_name, getattr(selected, field_name))
                continue
        current_value = getattr(master, field_name)
        if not current_value:
            value, _ = _pick_field_value(people, field_name)
            setattr(master, field_name, value)
    master.save()

    for duplicate in duplicates:
        moved_relations["users_person"] += User.objects.filter(person=duplicate).update(person=master)
        moved_relations["user_links_suggested"] += UserPersonLink.objects.filter(suggested_person=duplicate).update(
            suggested_person=master
        )
        moved_relations["family_contact_person"] += Family.objects.filter(contact_person=duplicate).update(contact_person=master)
        moved_relations["family_members"] += FamilyMember.objects.filter(person=duplicate).update(person=master)

        duplicate_coach = Coach.objects.filter(person=duplicate).first()
        master_coach = Coach.objects.filter(person=master).first()
        if duplicate_coach and not master_coach:
            duplicate_coach.person = master
            duplicate_coach.save(update_fields=["person"])
            moved_relations["coach_rebound"] += 1
        elif duplicate_coach and master_coach:
            duplicate_coach.delete()
            moved_relations["coach_deleted"] += 1

        duplicate_client = PotentialClient.objects.filter(person=duplicate).first()
        master_client = PotentialClient.objects.filter(person=master).first()
        if duplicate_client and not master_client:
            duplicate_client.person = master
            duplicate_client.save(update_fields=["person"])
            moved_relations["potential_client_rebound"] += 1
        elif duplicate_client and master_client:
            duplicate_client.delete()
            moved_relations["potential_client_deleted"] += 1

        athlete_moved = _merge_athlete(master, duplicate)
        for key, value in athlete_moved.items():
            moved_relations[f"athlete_{key}"] += value

        PersonMergeRedirect.objects.update_or_create(
            source_person=duplicate,
            defaults={"target_person": master, "job": cluster.job, "is_active": True},
        )

    cluster.status = PersonDuplicateCluster.STATUS_AUTO_MERGED if auto else PersonDuplicateCluster.STATUS_MERGED
    cluster.requires_manual_review = False
    cluster.save(update_fields=["status", "requires_manual_review", "updated_at"])

    return PersonMergeLog.objects.create(
        cluster=cluster,
        job=cluster.job,
        master_person=master,
        merged_person_ids=[person.id for person in duplicates],
        field_resolution=field_resolution,
        moved_relations=dict(moved_relations),
        notes=note,
        created_by=actor,
    )


def run_dedup_job(job: PersonDedupJob) -> PersonDedupJob:
    job = PersonDedupJob.objects.get(pk=job.pk)
    job.mark_running()
    job.log = ["job_started"]
    job.save(update_fields=["status", "started_at", "log", "updated_at"])

    try:
        PersonDuplicateCluster.objects.filter(job=job).delete()
        active_redirect_sources = set(
            PersonMergeRedirect.objects.filter(is_active=True).values_list("source_person_id", flat=True)
        )
        persons = list(
            Person.objects.select_related("club")
            .exclude(id__in=active_redirect_sources)
            .order_by("id")
        )
        job.total_persons = len(persons)
        job.processed_persons = len(persons)
        blocks = _build_blocks(persons)
        pair_scores: list[PairScore] = []
        seen_pairs: set[tuple[int, int]] = set()

        for block_people in blocks.values():
            if len(block_people) < 2:
                continue
            for idx, left in enumerate(block_people):
                for right in block_people[idx + 1:]:
                    pair_key = (min(left.id, right.id), max(left.id, right.id))
                    if pair_key in seen_pairs:
                        continue
                    seen_pairs.add(pair_key)
                    pair = _score_pair(left, right)
                    if pair.score >= job.min_score:
                        pair_scores.append(pair)

        union_find = _UnionFind([person.id for person in persons])
        for pair in pair_scores:
            union_find.union(pair.left_id, pair.right_id)

        grouped: dict[int, list[int]] = defaultdict(list)
        for person in persons:
            grouped[union_find.find(person.id)].append(person.id)
        grouped = {key: ids for key, ids in grouped.items() if len(ids) > 1}

        person_map = {person.id: person for person in persons}
        by_component_pairs: dict[int, list[PairScore]] = defaultdict(list)
        for pair in pair_scores:
            component_id = union_find.find(pair.left_id)
            by_component_pairs[component_id].append(pair)

        conflicts = 0
        auto_merged = 0
        for component_id, person_ids in grouped.items():
            people = [person_map[person_id] for person_id in person_ids]
            component_pairs = by_component_pairs.get(component_id, [])
            max_pair_score = max((pair.score for pair in component_pairs), default=0)
            reason_summary = ", ".join(component_pairs[0].reasons) if component_pairs else ""
            suggested_master = _suggest_master_person(people)
            requires_manual = True
            if job.mode == PersonDedupJob.MODE_AUTO_SAFE and _is_safe_auto_merge(people, max_pair_score, job.auto_merge_score):
                requires_manual = False

            cluster = PersonDuplicateCluster.objects.create(
                job=job,
                status=PersonDuplicateCluster.STATUS_OPEN,
                confidence=max_pair_score,
                reason_summary=reason_summary,
                max_pair_score=max_pair_score,
                candidate_pairs=len(component_pairs),
                requires_manual_review=requires_manual,
            )

            aggregate_score_map = defaultdict(int)
            reason_map: dict[int, set[str]] = defaultdict(set)
            for pair in component_pairs:
                aggregate_score_map[pair.left_id] += pair.score
                aggregate_score_map[pair.right_id] += pair.score
                for reason in pair.reasons:
                    reason_map[pair.left_id].add(reason)
                    reason_map[pair.right_id].add(reason)

            for person in people:
                PersonDuplicateItem.objects.create(
                    cluster=cluster,
                    person=person,
                    aggregate_score=aggregate_score_map.get(person.id, 0),
                    reasons=sorted(reason_map.get(person.id, set())),
                    is_suggested_master=person.id == suggested_master.id,
                )

            if requires_manual:
                conflicts += 1
                continue
            if job.dry_run:
                continue
            apply_cluster_merge(cluster, suggested_master.id, actor=job.created_by, auto=True, note="auto_safe")
            auto_merged += 1

        job.duplicate_clusters_found = len(grouped)
        job.auto_merged_clusters = auto_merged
        job.conflicts_count = conflicts
        job.log = [*job.log, f"clusters={len(grouped)}", f"auto_merged={auto_merged}", f"conflicts={conflicts}"]
        job.mark_finished(status=PersonDedupJob.STATUS_COMPLETED)
        job.save(
            update_fields=[
                "processed_persons",
                "total_persons",
                "duplicate_clusters_found",
                "auto_merged_clusters",
                "conflicts_count",
                "log",
                "status",
                "error_message",
                "finished_at",
                "updated_at",
            ]
        )
    except Exception as exc:
        job.log = [*job.log, "job_failed"]
        job.mark_finished(status=PersonDedupJob.STATUS_FAILED, error_message=str(exc))
        job.save(update_fields=["status", "error_message", "finished_at", "log", "updated_at"])
    return job


def build_job_payload(job: PersonDedupJob) -> dict:
    latest_clusters = job.clusters.values("status").annotate(count=Count("id"))
    cluster_stats = {row["status"]: row["count"] for row in latest_clusters}
    return {
        "id": job.id,
        "status": job.status,
        "mode": job.mode,
        "dry_run": job.dry_run,
        "min_score": job.min_score,
        "auto_merge_score": job.auto_merge_score,
        "total_persons": job.total_persons,
        "processed_persons": job.processed_persons,
        "duplicate_clusters_found": job.duplicate_clusters_found,
        "auto_merged_clusters": job.auto_merged_clusters,
        "conflicts_count": job.conflicts_count,
        "error_message": job.error_message,
        "log": job.log or [],
        "cluster_stats": cluster_stats,
        "created_at": timezone.localtime(job.created_at).strftime("%d.%m.%Y %H:%M"),
        "started_at": timezone.localtime(job.started_at).strftime("%d.%m.%Y %H:%M") if job.started_at else "",
        "finished_at": timezone.localtime(job.finished_at).strftime("%d.%m.%Y %H:%M") if job.finished_at else "",
    }


def build_cluster_payload(cluster: PersonDuplicateCluster) -> dict:
    items = list(cluster.items.select_related("person__club").order_by("-aggregate_score", "person__surname", "person__name"))
    return {
        "id": cluster.id,
        "status": cluster.status,
        "confidence": cluster.confidence,
        "max_pair_score": cluster.max_pair_score,
        "reason_summary": cluster.reason_summary,
        "requires_manual_review": cluster.requires_manual_review,
        "items": [
            {
                "person_id": item.person_id,
                "full_name": str(item.person),
                "surname": item.person.surname,
                "name": item.person.name,
                "middlename": item.person.middlename or "",
                "date_of_birth": item.person.date_of_birth.strftime("%Y-%m-%d") if item.person.date_of_birth else "",
                "gender": item.person.get_gender_display() if item.person.gender else "",
                "phone": item.person.phone or "",
                "email": item.person.email or "",
                "telegram": item.person.telegram or "",
                "club": item.person.club.name if item.person.club else "",
                "comment": item.person.comment or "",
                "aggregate_score": item.aggregate_score,
                "reasons": item.reasons or [],
                "is_suggested_master": item.is_suggested_master,
                "is_redirected": _is_redirected(item.person_id),
            }
            for item in items
        ],
    }
