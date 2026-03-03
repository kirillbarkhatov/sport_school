from datetime import date
from decimal import Decimal
import re

from django.contrib import messages
from django.db import transaction
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView, DeleteView, TemplateView

from users.mixins import ApprovedUserRequiredMixin
from users.utils import get_person_queryset_for_user
from school.forms import (
    PersonForm,
    FamilyForm,
    FamilyMemberInlineFormSet,
    ExistingFamilyMemberFormSet,
    FamilyAthleteProfileFormSet,
    FamilyServiceForm,
    FamilyPaymentForm,
    AthleteContractForm,
)
from school.models import Person, Family, FamilyMember, Athlete, FamilyAthleteProfile, FamilyService, FamilyPayment, Club
from school.models import DiscountType, ServiceType, AthleteContract
from bot.models import TelegramParticipant
from school.services import (
    ensure_monthly_service_for_contract,
    get_month_range,
)
from members.models import PersonDedupJob, PersonDuplicateCluster, PersonMergeRedirect
from members.services import apply_cluster_merge, build_cluster_payload, build_job_payload
from members.tasks import run_person_dedup_job_task
from users.constants import ADMIN_GROUP_NAME, MANAGER_GROUP_NAME
from users.models import User, UserPersonLink, UserPersonLinkStatus
from users.telegram_identity import normalize_phone, normalize_username, parse_telegram_reference, telegram_url_from_username


NON_ALNUM_RE = re.compile(r"[^a-zа-яё0-9]+", re.IGNORECASE)
NAME_PART_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
LATIN_RE = re.compile(r"[a-z]", re.IGNORECASE)

LATIN_TO_CYR_MULTI = (
    ("shch", "щ"),
    ("sch", "щ"),
    ("zh", "ж"),
    ("kh", "х"),
    ("ts", "ц"),
    ("ch", "ч"),
    ("sh", "ш"),
    ("yu", "ю"),
    ("ya", "я"),
    ("yo", "ё"),
    ("ye", "е"),
    ("yi", "и"),
    ("iy", "ий"),
)

LATIN_TO_CYR_SINGLE = {
    "a": "а",
    "b": "б",
    "c": "к",
    "d": "д",
    "e": "е",
    "f": "ф",
    "g": "г",
    "h": "х",
    "i": "и",
    "j": "й",
    "k": "к",
    "l": "л",
    "m": "м",
    "n": "н",
    "o": "о",
    "p": "п",
    "q": "к",
    "r": "р",
    "s": "с",
    "t": "т",
    "u": "у",
    "v": "в",
    "w": "в",
    "x": "кс",
    "y": "и",
    "z": "з",
}


def _normalize_name_token(value: str | None) -> str:
    if not value:
        return ""
    normalized = str(value).strip().lower().replace("ё", "е")
    return NON_ALNUM_RE.sub("", normalized)


def _latin_to_cyrillic(value: str | None) -> str:
    if not value:
        return ""
    src = str(value).strip().lower()
    if not src:
        return ""

    output: list[str] = []
    i = 0
    while i < len(src):
        matched = False
        for latin, cyr in LATIN_TO_CYR_MULTI:
            if src.startswith(latin, i):
                output.append(cyr)
                i += len(latin)
                matched = True
                break
        if matched:
            continue

        ch = src[i]
        output.append(LATIN_TO_CYR_SINGLE.get(ch, ch))
        i += 1

    return "".join(output)


def _extract_name_tokens(*values: str | None) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value:
            continue
        for raw in NAME_PART_RE.findall(str(value).lower()):
            token = _normalize_name_token(raw)
            if not token or token in seen:
                continue
            seen.add(token)
            tokens.append(token)
    return tokens


def _can_manage_people_records(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.groups.filter(name__in={ADMIN_GROUP_NAME, MANAGER_GROUP_NAME}).exists()


def _group_users_by_person(person_ids):
    if not person_ids:
        return {}
    from users.models import User

    users = (
        User.objects.select_related("person")
        .filter(person_id__in=person_ids)
        .order_by("person__surname", "person__name", "pk")
    )
    grouped = {}
    for user in users:
        grouped.setdefault(user.person_id, []).append(user)
    return grouped


# CRUD для модели "Person"
class PersonListView(ApprovedUserRequiredMixin, ListView):
    """Контроллер для работы с БД членов клуба - список"""

    model = Person
    template_name = "members/person_list.html"

    def get_queryset(self):
        redirected_sources = PersonMergeRedirect.objects.filter(is_active=True).values_list("source_person_id", flat=True)
        qs = (
            get_person_queryset_for_user(self.request.user)
            .exclude(id__in=redirected_sources)
            .prefetch_related("familymember_set__family", "linked_users")
        )
        club_id = self.request.GET.get("club")
        if club_id:
            qs = qs.filter(club_id=club_id)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        can_manage = self.request.user.is_staff or self.request.user.is_superuser
        context["can_manage_people"] = can_manage
        context["clubs"] = Club.objects.order_by("name")
        context["selected_club"] = self.request.GET.get("club") or ""
        if can_manage:
            context["families"] = list(Family.objects.order_by("family_name"))
            context["relation_choices"] = FamilyMember.FAMILY_RELATION
            context["person_form"] = PersonForm()
            latest_job = PersonDedupJob.objects.order_by("-created_at").first()
            context["dedup_last_job"] = latest_job
            context["dedup_open_clusters"] = PersonDuplicateCluster.objects.filter(
                job=latest_job,
                status=PersonDuplicateCluster.STATUS_OPEN,
            ).count() if latest_job else 0
            context["dedup_url"] = reverse("members:person_dedup_center")
        return context


class PersonDetailView(ApprovedUserRequiredMixin, DetailView):
    """Контроллер для работы с БД членов клуба - инфо о персоне"""

    model = Person
    template_name = "members/person_detail.html"

    def get_queryset(self):
        return get_person_queryset_for_user(self.request.user).prefetch_related("familymember_set__family")


class PersonCreateView(ApprovedUserRequiredMixin, CreateView):
    """Контроллер для работы с БД членов клуба - создание"""

    model = Person
    template_name = "members/person_form.html"
    form_class = PersonForm
    success_url = reverse_lazy("members:members_list")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def _is_ajax(self) -> bool:
        return self.request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"

    def form_invalid(self, form):
        response = super().form_invalid(form)
        if self._is_ajax():
            errors = {}
            for field, messages_list in form.errors.get_json_data().items():
                errors[field] = [message["message"] for message in messages_list]
            return JsonResponse({"success": False, "errors": errors}, status=400)
        return response

    def form_valid(self, form):
        request = self.request
        family_id = request.POST.get("family_id") or ""
        relation = (request.POST.get("relation") or "").strip()
        make_contact = request.POST.get("make_family_contact") in {"on", "true", "1"}

        selected_family = None
        membership_payload = None

        if family_id:
            try:
                selected_family = Family.objects.get(pk=family_id)
            except (Family.DoesNotExist, ValueError):
                form.add_error(None, "Выбранная семья не найдена.")
                return self.form_invalid(form)

            valid_relations = {value for value, _ in FamilyMember.FAMILY_RELATION}
            if relation not in valid_relations:
                form.add_error(None, "Укажите корректное родственное отношение.")
                return self.form_invalid(form)
        elif relation:
            form.add_error(None, "Для указанного родства выберите семью.")
            return self.form_invalid(form)

        if make_contact and not selected_family:
            form.add_error(None, "Чтобы сделать человека контактом, необходимо выбрать семью.")
            return self.form_invalid(form)

        with transaction.atomic():
            self.object = form.save()

            if selected_family:
                membership, _ = FamilyMember.objects.get_or_create(
                    family=selected_family,
                    person=self.object,
                    defaults={"relation": relation},
                )
                if membership.relation != relation:
                    membership.relation = relation
                    membership.save(update_fields=["relation"])
                if make_contact and selected_family.contact_person_id != self.object.pk:
                    selected_family.contact_person = self.object
                    selected_family.save(update_fields=["contact_person"])

                membership_payload = {
                    "family_id": selected_family.pk,
                    "family_name": selected_family.family_name or "Без названия",
                    "relation": membership.relation,
                    "relation_display": dict(FamilyMember.FAMILY_RELATION).get(
                        membership.relation, membership.relation
                    ),
                }

        messages.success(request, "Участник успешно добавлен.")

        if self._is_ajax():
            return JsonResponse(
                {
                    "success": True,
                    "person_id": self.object.pk,
                    "redirect_url": str(self.get_success_url()),
                    "membership": membership_payload,
                    "message": "Участник успешно добавлен.",
                },
                status=201,
            )

        return redirect(self.get_success_url())


class PersonUpdateView(ApprovedUserRequiredMixin, UpdateView):
    """Контроллер для работы с БД членов клуба - изменение"""

    model = Person
    template_name = "members/person_form.html"
    form_class = PersonForm

    def get_queryset(self):
        return get_person_queryset_for_user(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


class PersonDeleteView(ApprovedUserRequiredMixin, DeleteView):
    """Контроллер для работы с БД членов клуба - удаление"""

    model = Person
    success_url = reverse_lazy("members:members_list")
    template_name = "members/person_confirm_delete.html"

    def get_queryset(self):
        return get_person_queryset_for_user(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


class TelegramInterlocutorListView(ApprovedUserRequiredMixin, TemplateView):
    template_name = "members/telegram_interlocutors.html"

    def dispatch(self, request, *args, **kwargs):
        if not _can_manage_people_records(request.user):
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def _participant_summary_rows(self):
        participant_rows = list(
            TelegramParticipant.objects
            .select_related("chat", "linked_person")
            .order_by("-last_seen", "-first_seen")
        )
        by_user_id: dict[int, dict] = {}

        for row in participant_rows:
            summary = by_user_id.get(row.user_id)
            if not summary:
                summary = {
                    "user_id": row.user_id,
                    "username": row.username or "",
                    "first_name": row.first_name or "",
                    "last_name": row.last_name or "",
                    "phone": row.phone or "",
                    "first_seen": row.first_seen,
                    "last_seen": row.last_seen,
                    "chat_titles": set(),
                    "chat_ids": set(),
                    "rows_count": 0,
                    "linked_person": row.linked_person,
                }
                by_user_id[row.user_id] = summary

            summary["rows_count"] += 1
            summary["chat_ids"].add(row.chat_id)
            summary["chat_titles"].add(row.chat.title or row.chat.username or str(row.chat.chat_id))
            if row.first_seen and row.first_seen < summary["first_seen"]:
                summary["first_seen"] = row.first_seen
            if row.last_seen and row.last_seen > summary["last_seen"]:
                summary["last_seen"] = row.last_seen
            if not summary["username"] and row.username:
                summary["username"] = row.username
            if not summary["first_name"] and row.first_name:
                summary["first_name"] = row.first_name
            if not summary["last_name"] and row.last_name:
                summary["last_name"] = row.last_name
            if not summary["phone"] and row.phone:
                summary["phone"] = row.phone
            if not summary["linked_person"] and row.linked_person:
                summary["linked_person"] = row.linked_person

        summaries = list(by_user_id.values())
        summaries.sort(key=lambda item: item["last_seen"] or timezone.now(), reverse=True)
        return summaries

    @staticmethod
    def _score_person_candidate(summary: dict, person: Person) -> tuple[int, list[str]]:
        score = 0
        reasons: list[str] = []

        if person.telegram_id and person.telegram_id == summary["user_id"]:
            return 100, ["telegram_id_exact"]

        summary_username = normalize_username(summary.get("username"))
        person_username, _, _ = parse_telegram_reference(person.telegram)
        if summary_username and person_username and summary_username == person_username:
            score += 70
            reasons.append("telegram_username_exact")

        summary_phone = normalize_phone(summary.get("phone"))
        person_phone = normalize_phone(person.phone)
        if summary_phone and person_phone and summary_phone == person_phone:
            score += 60
            reasons.append("phone_exact")

        summary_last_raw = summary.get("last_name")
        summary_first_raw = summary.get("first_name")
        has_latin_name = bool(
            LATIN_RE.search(str(summary_last_raw or ""))
            or LATIN_RE.search(str(summary_first_raw or ""))
        )
        summary_last = _normalize_name_token(summary_last_raw)
        summary_first = _normalize_name_token(summary_first_raw)
        person_last = _normalize_name_token(person.surname)
        person_first = _normalize_name_token(person.name)
        name_tokens = _extract_name_tokens(summary_last_raw, summary_first_raw)

        exact_pairs: set[tuple[str, str]] = set()
        if summary_last and summary_first:
            exact_pairs.add((summary_last, summary_first))
            # Telegram often swaps first_name and last_name.
            exact_pairs.add((summary_first, summary_last))
        if len(name_tokens) >= 2:
            exact_pairs.add((name_tokens[0], name_tokens[1]))
            exact_pairs.add((name_tokens[1], name_tokens[0]))

        translit_pairs = {
            (
                _normalize_name_token(_latin_to_cyrillic(last)),
                _normalize_name_token(_latin_to_cyrillic(first)),
            )
            for last, first in exact_pairs
        }
        exact_tokens = {token for token in [summary_last, summary_first, *name_tokens] if token}
        translit_tokens = {
            _normalize_name_token(_latin_to_cyrillic(token))
            for token in exact_tokens
        }
        translit_tokens.discard("")

        if (person_last, person_first) in exact_pairs:
            score += 50
            reasons.append("full_name_exact")
        else:
            if person_last and person_last in exact_tokens:
                score += 30
                reasons.append("surname_exact")
            if person_first and person_first in exact_tokens:
                score += 20
                reasons.append("name_exact")

        # Additional match by transliterated latin -> cyrillic names from Telegram profile.
        if has_latin_name:
            if person_last and person_first and (person_last, person_first) in translit_pairs:
                score += 35
                reasons.append("full_name_translit")
            else:
                if person_last and person_last in translit_tokens:
                    score += 20
                    reasons.append("surname_translit")
                if person_first and person_first in translit_tokens:
                    score += 15
                    reasons.append("name_translit")

        return score, reasons

    def _build_candidates(self, summary: dict, people: list[Person]) -> list[dict]:
        candidates = []
        for person in people:
            has_telegram = bool(person.telegram_id) or bool((person.telegram or "").strip())
            if has_telegram:
                continue
            score, reasons = self._score_person_candidate(summary, person)
            if score <= 0:
                continue
            candidates.append({
                "person": person,
                "score": score,
                "reasons": reasons,
            })
        candidates.sort(key=lambda item: (item["score"], item["person"].id), reverse=True)
        return candidates[:5]

    def _link_interlocutor(self, *, user_id: int, person: Person, actor: User) -> None:
        now = timezone.now()
        rows = list(
            TelegramParticipant.objects
            .filter(user_id=user_id)
            .select_related("linked_person")
            .order_by("-last_seen")
        )
        if not rows:
            return

        TelegramParticipant.objects.filter(user_id=user_id).update(
            linked_person=person,
            linked_by=actor,
            linked_at=now,
        )

        latest = rows[0]
        person_updates: list[str] = []
        if not person.telegram_id:
            person.telegram_id = user_id
            person_updates.append("telegram_id")
        if not person.telegram and latest.username:
            person.telegram = telegram_url_from_username(latest.username)
            person_updates.append("telegram")
        if not person.phone and latest.phone:
            person.phone = latest.phone
            person_updates.append("phone")
        if person_updates:
            person.save(update_fields=person_updates)

        user = User.objects.filter(tg_id=user_id).first()
        if not user:
            return

        user_updates: list[str] = []
        if user.person_id != person.id:
            user.person = person
            user_updates.append("person")
        if not user.is_approved:
            user.is_approved = True
            user_updates.append("is_approved")
        if user_updates:
            user.save(update_fields=user_updates)

        link, _ = UserPersonLink.objects.get_or_create(user=user)
        link.suggested_person = person
        reasons = set(link.matched_reasons or [])
        reasons.add("manual_interlocutor_link")
        if person.telegram_id and user.tg_id and person.telegram_id == user.tg_id:
            reasons.add("telegram_id_exact")
        link.matched_reasons = sorted(reasons)
        link.apply_decision(UserPersonLinkStatus.APPROVED, decided_by=actor)
        link.save(update_fields=[
            "suggested_person",
            "matched_reasons",
            "status",
            "decided_by",
            "decided_at",
            "decision_note",
            "updated_at",
        ])

    def post(self, request, *args, **kwargs):
        action = (request.POST.get("action") or "").strip()
        user_id_raw = request.POST.get("user_id")
        try:
            user_id = int(user_id_raw)
        except (TypeError, ValueError):
            messages.error(request, "Некорректный идентификатор собеседника.")
            return redirect("members:telegram_interlocutors")

        if action == "bind_existing":
            person_id = request.POST.get("person_id")
            person = get_object_or_404(Person, pk=person_id)
            with transaction.atomic():
                self._link_interlocutor(user_id=user_id, person=person, actor=request.user)
            messages.success(request, f"Собеседник {user_id} привязан к {person}.")
            return redirect("members:telegram_interlocutors")

        if action == "create_person":
            gender = (request.POST.get("gender") or "male").strip()
            if gender not in {choice[0] for choice in Person.GENDER_CHOICES}:
                gender = "male"

            participant = (
                TelegramParticipant.objects
                .filter(user_id=user_id)
                .order_by("-last_seen")
                .first()
            )
            if not participant:
                messages.error(request, "Собеседник не найден.")
                return redirect("members:telegram_interlocutors")

            surname = (participant.last_name or "").strip()
            name = (participant.first_name or "").strip()
            if not surname and not name and participant.username:
                surname = participant.username
                name = "Telegram"
            if not surname:
                surname = "Пользователь"
            if not name:
                name = "Telegram"

            with transaction.atomic():
                person = Person.objects.create(
                    surname=surname[:100],
                    name=name[:100],
                    middlename="",
                    phone=(participant.phone or "")[:15] or None,
                    telegram=telegram_url_from_username(participant.username) if participant.username else "",
                    telegram_id=user_id,
                    gender=gender,
                )
                self._link_interlocutor(user_id=user_id, person=person, actor=request.user)

            messages.success(request, f"Создана новая персона {person} и выполнена привязка.")
            return redirect("members:telegram_interlocutors")

        messages.error(request, "Неизвестное действие.")
        return redirect("members:telegram_interlocutors")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        show_linked = self.request.GET.get("show") == "all"
        redirected_sources = PersonMergeRedirect.objects.filter(is_active=True).values_list("source_person_id", flat=True)
        summaries = self._participant_summary_rows()
        people = list(
            Person.objects
            .exclude(id__in=redirected_sources)
            .order_by("surname", "name")
        )
        unlinked_people = list(
            Person.objects
            .exclude(id__in=redirected_sources)
            .filter(linked_users__isnull=True, telegram_participant_links__isnull=True)
            .order_by("surname", "name")
            .distinct()
        )

        rows = []
        for summary in summaries:
            if summary["linked_person"] and not show_linked:
                continue
            candidates = self._build_candidates(summary, people)
            rows.append({
                **summary,
                "chat_titles": sorted(summary["chat_titles"]),
                "chat_count": len(summary["chat_ids"]),
                "candidates": candidates,
            })

        context["rows"] = rows
        context["unlinked_people"] = unlinked_people
        context["show_linked"] = show_linked
        context["gender_choices"] = Person.GENDER_CHOICES
        context["total_count"] = len(summaries)
        context["unlinked_count"] = sum(1 for item in summaries if not item["linked_person"])
        return context


class FamilyListView(ApprovedUserRequiredMixin, ListView):
    model = Family
    template_name = "members/family_list.html"

    def get_queryset(self):
        base_qs = Family.objects.prefetch_related("members__person")
        club_id = self.request.GET.get("club")
        if club_id:
            base_qs = base_qs.filter(members__person__club_id=club_id).distinct()
        if self.request.user.is_staff or self.request.user.is_superuser:
            return base_qs.order_by("family_name")
        family_ids = self.request.user.get_accessible_family_ids()
        return base_qs.filter(id__in=family_ids).order_by("family_name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        families = list(self.get_queryset())
        context["active_families"] = [f for f in families if f.status == Family.STATUS_ACTIVE]
        context["alumni_families"] = [f for f in families if f.status == Family.STATUS_ALUMNI]
        context["family_form"] = getattr(self, "family_form", FamilyForm())
        context["member_formset"] = getattr(
            self,
            "member_formset",
            FamilyMemberInlineFormSet(prefix="members"),
        )
        context["clubs"] = Club.objects.order_by("name")
        context["selected_club"] = self.request.GET.get("club") or ""
        person_ids = {
            membership.person_id
            for family in families
            for membership in family.members.all()
            if membership.person_id
        }
        linked_map = _group_users_by_person(person_ids)
        context["persons_with_accounts"] = list(linked_map.keys())
        if self.request.user.is_staff or self.request.user.is_superuser:
            self._attach_candidate_people(families)
        return context

    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()

        self.family_form = FamilyForm(request.POST)
        self.member_formset = FamilyMemberInlineFormSet(request.POST, prefix="members")

        if self.family_form.is_valid() and self.member_formset.is_valid():
            with transaction.atomic():
                family = self.family_form.save()
                self._create_members(family, self.member_formset)
            messages.success(request, "Семья успешно создана")
            return redirect("members:family_detail", pk=family.pk)

        return self.get(request, *args, **kwargs)

    def _create_members(self, family: Family, formset):
        for form in formset:
            if not form.cleaned_data or not any(form.cleaned_data.values()):
                continue
            person = form.cleaned_data.get("existing_person")
            if not person:
                person = Person.objects.create(
                    surname=form.cleaned_data.get("surname"),
                    name=form.cleaned_data.get("name"),
                    middlename=form.cleaned_data.get("middlename"),
                    date_of_birth=form.cleaned_data.get("date_of_birth") or date(1970, 1, 1),
                    gender=form.cleaned_data.get("gender") or Person.GENDER_CHOICES[0][0],
                )

            relation = form.cleaned_data.get("relation")
            member, created = FamilyMember.objects.get_or_create(
                family=family,
                person=person,
                defaults={"relation": relation},
            )
            if not created and relation:
                member.relation = relation
                member.save(update_fields=["relation"])

            if form.cleaned_data.get("is_athlete") and not hasattr(person, "athlete"):
                athlete = Athlete.objects.create(
                    person=person,
                    level=Athlete.LEVEL_CHOICES[0][0],
                )
            elif hasattr(person, "athlete"):
                athlete = person.athlete
            else:
                athlete = None

            if athlete:
                profile, created = FamilyAthleteProfile.objects.get_or_create(
                    family=family,
                    athlete=athlete,
                    defaults={
                        "monthly_fee": Decimal("0.00"),
                        "discount_type": DiscountType.NONE,
                        "discount_value": Decimal("0.00"),
                    },
                )

    def _attach_candidate_people(self, families: list[Family]) -> None:
        people = list(Person.objects.all().order_by("surname", "name"))
        for family in families:
            existing_ids = {membership.person_id for membership in family.members.all()}
            candidates = [person for person in people if person.id not in existing_ids]
            family_prefix = (family.family_name or "").strip().lower()[:3]
            candidates.sort(
                key=lambda person: (
                    0
                    if family_prefix
                    and (person.surname or "").strip().lower()[:3] == family_prefix
                    else 1,
                    person.surname.lower(),
                    person.name.lower(),
                )
            )
            family.candidate_people = candidates
            family.relation_choices = FamilyMember.FAMILY_RELATION


class FamilyDetailView(ApprovedUserRequiredMixin, DetailView):
    model = Family
    template_name = "members/family_detail.html"

    def get_queryset(self):
        base = Family.objects.prefetch_related("members__person")
        if self.request.user.is_staff or self.request.user.is_superuser:
            return base
        return base.filter(id__in=self.request.user.get_accessible_family_ids())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        family: Family = self.object
        memberships = list(family.members.select_related("person"))
        # ensure profiles exist для семейных спортсменов
        for membership in memberships:
            person = membership.person
            if hasattr(person, "athlete"):
                profile, _ = FamilyAthleteProfile.objects.get_or_create(
                    family=family,
                    athlete=person.athlete,
                    defaults={
                        "monthly_fee": Decimal("0.00"),
                        "discount_type": DiscountType.NONE,
                        "discount_value": Decimal("0.00"),
                    },
                )
        profiles = (
            FamilyAthleteProfile.objects.filter(family=family)
            .select_related("athlete__person")
            .prefetch_related("services__payments", "contracts")
        )

        person_ids = {m.person_id for m in memberships if m.person_id}
        linked_map = _group_users_by_person(person_ids)
        linked_account_rows = [
            {
                "membership": membership,
                "users": linked_map.get(membership.person_id, []),
            }
            for membership in memberships
            if membership.person_id and linked_map.get(membership.person_id)
        ]

        today = timezone.now().date()
        month_start, _, next_month = get_month_range(today)

        athlete_rows = []
        for profile in profiles:
            contracts = list(profile.contracts.all())
            active_contract = next((contract for contract in contracts if contract.status == "active"), None)
            if active_contract:
                ensure_monthly_service_for_contract(active_contract, today)

            target_contract = active_contract or (contracts[0] if contracts else None)

            month_services_qs = profile.services.filter(
                service_type=ServiceType.MONTHLY,
                created_at__date__gte=month_start,
                created_at__date__lt=next_month,
            )
            if target_contract:
                month_services_qs = month_services_qs.filter(contract=target_contract)

            month_services = list(month_services_qs)
            amount_due = sum((service.amount - service.discount_value for service in month_services if not service.is_closed), Decimal("0.00"))
            amount_paid = Decimal("0.00")
            for service in month_services:
                amount_paid += sum(
                    (
                        payment.amount
                        for payment in service.payments.filter(
                            paid_at__gte=month_start,
                            paid_at__lt=next_month,
                        )
                    ),
                    Decimal("0.00"),
                )
            balance = amount_due - amount_paid

            if target_contract:
                contract_status = target_contract.status
                contract_fee = target_contract.base_fee
                contract_discount = target_contract.discount_value
            else:
                contract_status = "missing"
                contract_fee = Decimal("0.00")
                contract_discount = Decimal("0.00")

            athlete_rows.append(
                {
                    "athlete": profile.athlete,
                    "profile": profile,
                    "contract": target_contract,
                    "contract_status": contract_status,
                    "contract_fee": contract_fee,
                    "contract_discount": contract_discount,
                    "amount_due": amount_due,
                    "amount_paid": amount_paid,
                    "balance": balance,
                    "contract_create_url": reverse("members:contract_create", args=[family.pk, profile.pk]),
                    "contract_edit_url": reverse("members:contract_update", args=[family.pk, target_contract.pk]) if active_contract else None,
                    "create_label": "Создать договор на следующий сезон" if active_contract else "Создать договор",
                }
            )

        services = (
            FamilyService.objects.filter(family=family)
            .select_related("profile__athlete__person")
            .prefetch_related("payments")
        )
        payments = FamilyPayment.objects.filter(service__family=family).select_related("service")
        service_rows = []
        for service in services:
            paid = sum((p.amount for p in service.payments.all()), Decimal("0.00"))
            net_amount = service.amount - service.discount_value
            service_rows.append(
                {
                    "service": service,
                    "net_amount": net_amount,
                    "paid_amount": paid,
                    "balance": net_amount - paid,
                }
            )

        context.update(
            athlete_rows=athlete_rows,
            service_rows=service_rows,
            linked_account_rows=linked_account_rows,
            persons_with_accounts=list(linked_map.keys()),
        )
        if self.request.user.is_staff or self.request.user.is_superuser:
            context["contact_people"] = Person.objects.order_by("surname", "name")
            context["status_choices"] = Family.STATUS_CHOICES
        return context


class FamilyUpdateView(ApprovedUserRequiredMixin, UpdateView):
    model = Family
    form_class = FamilyForm
    template_name = "members/family_form.html"
    success_url = reverse_lazy("members:family_list")

    def get_queryset(self):
        if self.request.user.is_staff or self.request.user.is_superuser:
            return Family.objects.all()
        return Family.objects.filter(id__in=self.request.user.get_accessible_family_ids())

    def get_success_url(self):
        return reverse("members:family_detail", kwargs={"pk": self.object.pk})

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context["member_formset"] = ExistingFamilyMemberFormSet(self.request.POST, instance=self.object, prefix="members")
            context["profile_formset"] = FamilyAthleteProfileFormSet(self.request.POST, instance=self.object, prefix="profiles")
        else:
            context["member_formset"] = ExistingFamilyMemberFormSet(instance=self.object, prefix="members")
            context["profile_formset"] = FamilyAthleteProfileFormSet(instance=self.object, prefix="profiles")
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        member_formset = context["member_formset"]
        profile_formset = context["profile_formset"]
        if member_formset.is_valid() and profile_formset.is_valid():
            with transaction.atomic():
                self.object = form.save()
                member_formset.instance = self.object
                profile_formset.instance = self.object
                member_formset.save()
                profile_formset.save()
            messages.success(self.request, "Данные семьи обновлены")
            return redirect(self.get_success_url())
        return self.render_to_response(self.get_context_data(form=form))


class FamilyFinanceView(ApprovedUserRequiredMixin, DetailView):
    model = Family
    template_name = "members/family_finance.html"

    def get_queryset(self):
        base = Family.objects.prefetch_related("services__payments", "athlete_profiles__athlete__person")
        if self.request.user.is_staff or self.request.user.is_superuser:
            return base
        return base.filter(id__in=self.request.user.get_accessible_family_ids())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        family = self.object
        services = family.services.select_related("profile__athlete__person").prefetch_related("payments")
        payments = FamilyPayment.objects.filter(service__family=family).select_related("service")
        service_rows = []
        for service in services:
            paid = sum((p.amount for p in service.payments.all()), Decimal("0.00"))
            net_amount = service.amount - service.discount_value
            service_rows.append({
                "service": service,
                "net_amount": net_amount,
                "paid_amount": paid,
                "balance": net_amount - paid,
            })

        context.update(
            service_rows=service_rows,
            service_form=FamilyServiceForm(family=family),
            payment_form=FamilyPaymentForm(family=family),
            payments=payments,
        )
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()

        action = request.POST.get("action")
        if action == "create_service":
            form = FamilyServiceForm(request.POST, family=self.object)
            if form.is_valid():
                service = form.save(commit=False)
                service.family = self.object
                service.save()
                messages.success(request, "Услуга добавлена")
                return redirect("members:family_finance", pk=self.object.pk)
            payment_form = FamilyPaymentForm(family=self.object)
            service_form = form
        elif action == "create_payment":
            form = FamilyPaymentForm(request.POST, family=self.object)
            if form.is_valid():
                form.save()
                messages.success(request, "Платёж добавлен")
                return redirect("members:family_finance", pk=self.object.pk)
            service_form = FamilyServiceForm(family=self.object)
            payment_form = form
        else:
            return redirect("members:family_finance", pk=self.object.pk)

        context = self.get_context_data()
        context["service_form"] = service_form
        context["payment_form"] = payment_form
        return self.render_to_response(context)


class FamilyServiceUpdateView(ApprovedUserRequiredMixin, UpdateView):
    model = FamilyService
    form_class = FamilyServiceForm
    template_name = "members/family_service_form.html"
    pk_url_kwarg = "service_pk"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        self.family = get_object_or_404(Family, pk=self.kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return FamilyService.objects.filter(family=self.family).select_related("profile__athlete__person")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["family"] = self.family
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["name"].widget.attrs.pop("readonly", None)
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["family"] = self.family
        return context

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, "Услуга обновлена")
        return redirect("members:family_finance", pk=self.family.pk)


class FamilyServiceDeleteView(ApprovedUserRequiredMixin, DeleteView):
    model = FamilyService
    pk_url_kwarg = "service_pk"
    template_name = "members/family_service_confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        self.family = get_object_or_404(Family, pk=self.kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return FamilyService.objects.filter(family=self.family)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["family"] = self.family
        return context

    def get_success_url(self):
        messages.success(self.request, "Счёт удалён")
        return reverse("members:family_finance", kwargs={"pk": self.family.pk})


class AthleteContractCreateView(ApprovedUserRequiredMixin, CreateView):
    form_class = AthleteContractForm
    template_name = "members/family_contract_create.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        self.family = get_object_or_404(Family, pk=self.kwargs["pk"])
        self.profile = get_object_or_404(FamilyAthleteProfile, pk=self.kwargs["profile_pk"], family=self.family)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["profile"] = self.profile
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"family": self.family, "profile": self.profile})
        return context

    def form_valid(self, form):
        contract = form.save(commit=False)
        contract.profile = self.profile
        contract.save()
        ensure_monthly_service_for_contract(contract)
        messages.success(self.request, "Договор создан")
        return redirect("members:family_detail", pk=self.family.pk)


class AthleteContractUpdateView(ApprovedUserRequiredMixin, UpdateView):
    model = AthleteContract
    form_class = AthleteContractForm
    template_name = "members/family_contract_update.html"
    pk_url_kwarg = "contract_pk"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        self.family = get_object_or_404(Family, pk=self.kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return AthleteContract.objects.filter(profile__family=self.family).select_related("profile__athlete__person")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["profile"] = self.object.profile
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"family": self.family, "profile": self.object.profile})
        return context

    def form_valid(self, form):
        contract = form.save()
        ensure_monthly_service_for_contract(contract)
        messages.success(self.request, "Договор обновлён")
        return redirect("members:family_detail", pk=self.family.pk)


class PersonToggleAthleteView(ApprovedUserRequiredMixin, View):
    """Переключение статуса спортсмена для человека."""

    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()

        person = get_object_or_404(Person, pk=self.kwargs["pk"])
        redirect_url = request.POST.get("next") or reverse("members:members_list")
        is_ajax = request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"

        if person.is_athlete:
            person.athlete.delete()
            message = f"{person} исключён из списка спортсменов."
            if is_ajax:
                return JsonResponse(
                    {
                        "success": True,
                        "is_athlete": False,
                        "message": message,
                    }
                )
            messages.success(request, message)
            return redirect(redirect_url)

        level_value = Athlete.LEVEL_CHOICES[-1][0]
        athlete = Athlete.objects.create(person=person, level=level_value)

        for membership in person.familymember_set.select_related("family"):
            family = membership.family
            FamilyAthleteProfile.objects.get_or_create(
                family=family,
                athlete=athlete,
                defaults={
                    "monthly_fee": Decimal("0.00"),
                    "discount_type": DiscountType.NONE,
                    "discount_value": Decimal("0.00"),
                },
            )

        message = f"{person} добавлен в список спортсменов."
        if is_ajax:
            return JsonResponse(
                {
                    "success": True,
                    "is_athlete": True,
                    "message": message,
                }
            )
        messages.success(request, message)
        return redirect(redirect_url)


class PersonAssignFamilyView(ApprovedUserRequiredMixin, View):
    """Назначение человека в семью или удаление связи."""

    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()

        person = get_object_or_404(Person, pk=self.kwargs["pk"])
        family_id = request.POST.get("family_id") or ""
        relation = (request.POST.get("relation") or "").strip()
        redirect_url = request.POST.get("next") or reverse("members:members_list")
        is_ajax = request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"

        existing_family_ids = list(person.familymember_set.values_list("family_id", flat=True))

        if not family_id:
            FamilyMember.objects.filter(person=person).delete()
            if person.is_athlete and existing_family_ids:
                FamilyAthleteProfile.objects.filter(
                    family_id__in=existing_family_ids,
                    athlete=person.athlete,
                ).delete()
            messages.success(request, f"Человек {person} удалён из семьи.")
            if is_ajax:
                return JsonResponse(
                    {
                        "success": True,
                        "membership": None,
                        "message": f"Человек {person} удалён из семьи.",
                    }
                )
            return redirect(redirect_url)

        family = get_object_or_404(Family, pk=family_id)
        valid_relations = {value for value, _ in FamilyMember.FAMILY_RELATION}
        if not relation or relation not in valid_relations:
            error_message = "Укажите корректное родственное отношение."
            if is_ajax:
                return JsonResponse({"success": False, "errors": [error_message]}, status=400)
            messages.error(request, error_message)
            return redirect(redirect_url)

        FamilyMember.objects.filter(person=person).exclude(family=family).delete()
        membership, created = FamilyMember.objects.get_or_create(
            family=family,
            person=person,
            defaults={"relation": FamilyMember.FAMILY_RELATION[0][0]},
        )
        updated = False
        if not created and membership.relation != relation:
            membership.relation = relation
            membership.save(update_fields=["relation"])
            updated = True
        elif created and membership.relation != relation:
            membership.relation = relation
            membership.save(update_fields=["relation"])

        if person.is_athlete:
            FamilyAthleteProfile.objects.filter(
                family_id__in=[fid for fid in existing_family_ids if fid != family.pk],
                athlete=person.athlete,
            ).delete()
            FamilyAthleteProfile.objects.get_or_create(
                family=family,
                athlete=person.athlete,
                defaults={
                    "monthly_fee": Decimal("0.00"),
                    "discount_type": DiscountType.NONE,
                    "discount_value": Decimal("0.00"),
                },
            )

        relation_display = dict(FamilyMember.FAMILY_RELATION).get(membership.relation, membership.relation)
        action = "добавлен" if created else "обновлён" if updated else "подтверждён"
        action_message = f"{person} {action} в семье {family}."
        messages.success(request, action_message)
        if is_ajax:
            return JsonResponse(
                {
                    "success": True,
                    "membership": {
                        "family_id": family.pk,
                        "family_name": family.family_name or "Без названия",
                        "relation": membership.relation,
                        "relation_display": relation_display,
                    },
                    "message": action_message,
                }
            )
        return redirect(redirect_url)


class FamilyToggleStatusView(ApprovedUserRequiredMixin, View):
    """Переключение статуса семьи между действующей и бывшей."""

    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()

        family = get_object_or_404(Family, pk=self.kwargs["pk"])
        next_status = Family.STATUS_ALUMNI if family.status == Family.STATUS_ACTIVE else Family.STATUS_ACTIVE
        family.status = next_status
        family.save(update_fields=["status"])
        status_label = dict(Family.STATUS_CHOICES).get(next_status, next_status)
        messages.success(request, f"Статус семьи обновлён: {status_label.lower()}.")
        redirect_url = request.POST.get("next") or reverse("members:family_list")
        return redirect(redirect_url)


class FamilyInlineUpdateView(ApprovedUserRequiredMixin, View):
    """Обновление отдельных полей семьи без полной формы."""

    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()

        family = get_object_or_404(Family, pk=self.kwargs["pk"])
        field = (request.POST.get("field") or "").strip()
        value = (request.POST.get("value") or "").strip()

        try:
            if field == "family_name":
                family.family_name = value or None
                family.full_clean()
                family.save(update_fields=["family_name"])
                return JsonResponse(
                    {
                        "success": True,
                        "family_name": family.family_name or "Без названия",
                        "message": "Название семьи обновлено.",
                    }
                )

            if field == "contact_person":
                contact_person = None
                if value:
                    contact_person = get_object_or_404(Person, pk=value)
                family.contact_person = contact_person
                family.full_clean()
                family.save(update_fields=["contact_person"])
                return JsonResponse(
                    {
                        "success": True,
                        "contact_person": str(contact_person) if contact_person else "Не указано",
                        "contact_person_id": contact_person.pk if contact_person else None,
                        "message": "Контактное лицо обновлено.",
                    }
                )

            if field == "status":
                valid_status = {value for value, _ in Family.STATUS_CHOICES}
                if value not in valid_status:
                    return JsonResponse(
                        {"success": False, "errors": ["Некорректный статус."]},
                        status=400,
                    )
                family.status = value
                family.full_clean()
                family.save(update_fields=["status"])
                return JsonResponse(
                    {
                        "success": True,
                        "status": family.status,
                        "status_display": family.get_status_display(),
                        "message": "Статус семьи обновлён.",
                    }
                )
        except ValidationError as exc:
            error_list = []
            for messages_list in exc.message_dict.values():
                error_list.extend(messages_list)
            error_list = error_list or [exc.message]
            return JsonResponse({"success": False, "errors": error_list}, status=400)

        return JsonResponse(
            {"success": False, "errors": ["Неподдерживаемое поле для обновления."]},
            status=400,
        )


class FamilyAddMemberView(ApprovedUserRequiredMixin, View):
    """Добавление существующего человека в семью."""

    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()

        family = get_object_or_404(Family, pk=self.kwargs["pk"])
        is_ajax = request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
        redirect_url = request.POST.get("next") or reverse("members:family_list")
        person_id = request.POST.get("person_id")
        relation = request.POST.get("relation")

        if not person_id or not relation:
            messages.error(request, "Выберите участника и укажите отношение.")
            if is_ajax:
                return JsonResponse(
                    {"success": False, "errors": ["Выберите участника и укажите отношение."]},
                    status=400,
                )
            return redirect(redirect_url)

        valid_relations = {value for value, _ in FamilyMember.FAMILY_RELATION}
        if relation not in valid_relations:
            messages.error(request, "Некорректный тип отношения.")
            if is_ajax:
                return JsonResponse(
                    {"success": False, "errors": ["Некорректный тип отношения."]},
                    status=400,
                )
            return redirect(redirect_url)

        person = get_object_or_404(Person, pk=person_id)
        membership, created = FamilyMember.objects.get_or_create(
            family=family,
            person=person,
            defaults={"relation": relation},
        )
        if not created and membership.relation != relation:
            membership.relation = relation
            membership.save(update_fields=["relation"])
            action_message = f"{person} обновлён в семье."
        elif created:
            action_message = f"{person} добавлен в семью."
        else:
            action_message = f"{person} уже состоит в семье."

        if person.is_athlete:
            FamilyAthleteProfile.objects.get_or_create(
                family=family,
                athlete=person.athlete,
                defaults={
                    "monthly_fee": Decimal("0.00"),
                    "discount_type": DiscountType.NONE,
                    "discount_value": Decimal("0.00"),
                },
            )

        relation_display = membership.get_relation_display()
        messages.success(request, action_message)
        if is_ajax:
            member_html = render_to_string(
                "members/includes/family_member_item.html",
                {"membership": membership},
                request=request,
            )
            return JsonResponse(
                {
                    "success": True,
                    "message": action_message,
                    "member": {
                        "id": membership.pk,
                        "person_id": person.pk,
                        "full_name": str(person),
                        "relation": membership.relation,
                        "relation_display": relation_display,
                        "html": member_html,
                        "created": created,
                    },
                }
            )
        return redirect(redirect_url)


class PersonDedupCenterView(ApprovedUserRequiredMixin, ListView):
    template_name = "members/person_dedup_center.html"
    model = PersonDedupJob
    context_object_name = "jobs"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return list(PersonDedupJob.objects.select_related("created_by").order_by("-created_at")[:10])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        latest_job = self.object_list[0] if self.object_list else None
        context["latest_job"] = latest_job
        context["latest_job_payload"] = build_job_payload(latest_job) if latest_job else None
        context["pending_clusters"] = (
            latest_job.clusters.filter(status=PersonDuplicateCluster.STATUS_OPEN).count()
            if latest_job
            else 0
        )
        return context


class PersonDedupStartView(ApprovedUserRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return JsonResponse({"success": False, "error": "forbidden"}, status=403)

        mode = request.POST.get("mode") or PersonDedupJob.MODE_ANALYZE
        if mode not in {choice[0] for choice in PersonDedupJob.MODE_CHOICES}:
            return JsonResponse({"success": False, "error": "bad_mode"}, status=400)

        dry_run = (request.POST.get("dry_run") or "1").lower() in {"1", "true", "on", "yes"}
        try:
            min_score = int(request.POST.get("min_score") or 55)
            auto_merge_score = int(request.POST.get("auto_merge_score") or 92)
        except ValueError:
            return JsonResponse({"success": False, "error": "bad_thresholds"}, status=400)

        job = PersonDedupJob.objects.create(
            mode=mode,
            dry_run=dry_run,
            min_score=max(1, min(100, min_score)),
            auto_merge_score=max(1, min(100, auto_merge_score)),
            created_by=request.user,
            status=PersonDedupJob.STATUS_QUEUED,
        )

        try:
            run_person_dedup_job_task.delay(job.id)
        except Exception:
            run_person_dedup_job_task(job.id)

        return JsonResponse({"success": True, "job": build_job_payload(PersonDedupJob.objects.get(pk=job.id))})


class PersonDedupJobStatusView(ApprovedUserRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return JsonResponse({"success": False, "error": "forbidden"}, status=403)
        job = get_object_or_404(PersonDedupJob, pk=kwargs["job_id"])
        return JsonResponse({"success": True, "job": build_job_payload(job)})


class PersonDedupClustersView(ApprovedUserRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return JsonResponse({"success": False, "error": "forbidden"}, status=403)

        job = get_object_or_404(PersonDedupJob, pk=kwargs["job_id"])
        status_filter = (request.GET.get("status") or "").strip()
        qs = job.clusters.order_by("-max_pair_score", "id").prefetch_related("items__person__club")
        if status_filter:
            qs = qs.filter(status=status_filter)

        payload = []
        for cluster in qs[:200]:
            cluster_data = build_cluster_payload(cluster)
            payload.append(
                {
                    "id": cluster_data["id"],
                    "status": cluster_data["status"],
                    "confidence": cluster_data["confidence"],
                    "max_pair_score": cluster_data["max_pair_score"],
                    "reason_summary": cluster_data["reason_summary"],
                    "requires_manual_review": cluster_data["requires_manual_review"],
                    "items": cluster_data["items"],
                }
            )
        return JsonResponse({"success": True, "clusters": payload})


class PersonDedupClusterDetailView(ApprovedUserRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return JsonResponse({"success": False, "error": "forbidden"}, status=403)
        cluster = get_object_or_404(
            PersonDuplicateCluster.objects.prefetch_related("items__person__club"),
            pk=kwargs["cluster_id"],
        )
        return JsonResponse({"success": True, "cluster": build_cluster_payload(cluster)})


class PersonDedupClusterSkipView(ApprovedUserRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return JsonResponse({"success": False, "error": "forbidden"}, status=403)
        cluster = get_object_or_404(PersonDuplicateCluster, pk=kwargs["cluster_id"])
        cluster.status = PersonDuplicateCluster.STATUS_SKIPPED
        cluster.requires_manual_review = False
        cluster.save(update_fields=["status", "requires_manual_review", "updated_at"])
        return JsonResponse({"success": True, "cluster": {"id": cluster.id, "status": cluster.status}})


class PersonDedupClusterMergeView(ApprovedUserRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return JsonResponse({"success": False, "error": "forbidden"}, status=403)

        cluster = get_object_or_404(
            PersonDuplicateCluster.objects.prefetch_related("items__person"),
            pk=kwargs["cluster_id"],
        )
        master_person_id_raw = request.POST.get("master_person_id")
        if not master_person_id_raw or not master_person_id_raw.isdigit():
            return JsonResponse({"success": False, "error": "bad_master_person"}, status=400)
        master_person_id = int(master_person_id_raw)

        field_resolution = {}
        for field_name in (
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
        ):
            field_key = f"field_{field_name}_person_id"
            selected_id = request.POST.get(field_key)
            if selected_id and str(selected_id).isdigit():
                field_resolution[field_name] = int(selected_id)

        note = (request.POST.get("note") or "").strip()
        try:
            log = apply_cluster_merge(
                cluster,
                master_person_id,
                actor=request.user,
                field_resolution=field_resolution,
                note=note,
            )
        except ValueError as exc:
            return JsonResponse({"success": False, "error": str(exc)}, status=400)

        return JsonResponse(
            {
                "success": True,
                "merge_log_id": log.id,
                "master_person_id": log.master_person_id,
                "merged_person_ids": log.merged_person_ids,
            }
        )
