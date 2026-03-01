import json
import secrets
from datetime import date
from io import BytesIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.db import transaction
from django.db.models import Prefetch, Q
from django.db.models.functions import Coalesce
from django.http import FileResponse, Http404, JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView, ListView

from school.document_ai import make_signed_document_url
from school.forms import (
    AthleteForm,
    AthleteCompactForm,
    PersonForm,
    PersonCompactForm,
    FamilyForm,
    FamilyMemberForm,
    CompetitionForm,
    CompetitionVenueForm,
    CompetitionApplyAthleteForm,
    CompetitionDocumentUploadForm,
    BulkDocumentUploadForm,
    DocumentAIAnalysisFilterForm,
)
from school.document_ai import validate_signed_document_token
from school.document_binding import (
    bind_document_to_athlete,
    bind_document_to_competition,
    create_athlete_from_analysis,
    create_competition_from_analysis,
    get_binding_context,
    infer_athlete_doc_type,
    sync_athlete_document_from_analysis,
)
from school.document_ingest import DocumentIngestValidationError, ingest_single_document
from school.models import (
    Athlete,
    Family,
    FamilyMember,
    Group,
    Club,
    Competition,
    CompetitionVenue,
    CompetitionEntry,
    CompetitionApplication,
    CompetitionApplicationLink,
    Person,
    CompetitionDocument,
    Document,
    DocumentAIAnalysis,
    DocumentType,
    AthleteDocument,
)
from school.tasks import (
    enqueue_documents_for_ai_analysis,
    monitor_athlete_document_analysis_status,
    rebind_unbound_competition_documents_task,
)
from users.constants import ADMIN_GROUP_NAME, COACH_GROUP_NAME, MANAGER_GROUP_NAME
from users.mixins import ApprovedUserRequiredMixin
from users.utils import (
    get_athlete_queryset_for_user,
    get_person_queryset_for_user,
    get_group_queryset_for_user,
    get_class_queryset_for_user,
    get_available_athlete_queryset_for_user,
    get_coach_club_ids_for_user,
    ensure_user_athlete_links,
)


# Create your views here.


KANAEV_CLUB_NAME = "Канаев Ски Клаб"


def _is_extended_athlete_filters_user(user) -> bool:
    if user.is_staff or user.is_superuser:
        return True
    role_names = {ADMIN_GROUP_NAME, COACH_GROUP_NAME, MANAGER_GROUP_NAME}
    return user.groups.filter(name__in=role_names).exists()


def _can_manage_athlete_certificates(user) -> bool:
    return bool(user and user.is_authenticated)


def _monitor_cache_key(athlete_document_id: int) -> str:
    return f"athlete-cert-monitor:{athlete_document_id}"


def _get_athlete_med_documents(athlete: Athlete):
    return (
        AthleteDocument.objects
        .select_related("document", "document__ai_analysis")
        .filter(athlete=athlete, doc_type=DocumentType.MED_CERT)
        .order_by("-is_actual", "-created_at")
    )


def _build_athlete_certificate_context(athlete: Athlete) -> dict[str, object]:
    med_docs = list(_get_athlete_med_documents(athlete))
    active_doc = next((item for item in med_docs if item.is_actual), None)
    inactive_docs = [item for item in med_docs if not item.is_actual]

    active_payload = None
    if active_doc:
        original_name = (active_doc.document.original_name or "").lower()
        mime_type = (active_doc.document.mime_type or "").lower()
        is_image = mime_type.startswith("image/") or original_name.endswith(
            (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic", ".heif")
        )
        active_payload = {
            "id": active_doc.id,
            "valid_until": active_doc.valid_until,
            "valid_until_iso": active_doc.valid_until.isoformat() if active_doc.valid_until else "",
            "needs_clarification": active_doc.needs_valid_until_clarification,
            "preview_url": make_signed_document_url(active_doc.document),
            "file_name": active_doc.document.original_name or "Документ",
            "preview_kind": "image" if is_image else "document",
        }

    inactive_payload = [
        {
            "id": item.id,
            "valid_until": item.valid_until,
            "preview_url": make_signed_document_url(item.document),
            "file_name": item.document.original_name or "Документ",
        }
        for item in inactive_docs
    ]

    return {
        "active_document": active_doc,
        "inactive_documents": inactive_docs,
        "active_payload": active_payload,
        "inactive_payload": inactive_payload,
        "has_active": bool(active_doc and active_doc.valid_until),
        "needs_clarification": bool(active_doc and active_doc.needs_valid_until_clarification),
    }


def _resolve_certificate_ui_state(active_document: AthleteDocument | None) -> dict[str, object]:
    if not active_document:
        return {
            "badge_text": "Данные отсутствуют",
            "badge_tone": "warning",
            "show_clarify": False,
            "is_unrecognized": False,
        }

    if active_document.valid_until:
        return {
            "badge_text": f"Действует до {active_document.valid_until.strftime('%d.%m.%Y')}",
            "badge_tone": "success",
            "show_clarify": False,
            "is_unrecognized": False,
        }

    analysis = getattr(active_document.document, "ai_analysis", None)
    if analysis and analysis.is_analyzed_successfully:
        is_med_cert = (
            analysis.doc_type == DocumentAIAnalysis.DocType.ATHLETE_SPECIFIC
            and infer_athlete_doc_type(analysis) == DocumentType.MED_CERT
        )
        if is_med_cert:
            return {
                "badge_text": "Укажите дату вручную",
                "badge_tone": "warning",
                "show_clarify": True,
                "is_unrecognized": False,
            }
        return {
            "badge_text": "Справка не распознана",
            "badge_tone": "danger",
            "show_clarify": False,
            "is_unrecognized": True,
        }

    return {
        "badge_text": "Данные отсутствуют",
        "badge_tone": "warning",
        "show_clarify": False,
        "is_unrecognized": False,
    }


class IndexView(ApprovedUserRequiredMixin, TemplateView):
    """Стартовая страница"""
    template_name = "school/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        person_qs = get_person_queryset_for_user(user)
        athlete_qs = get_athlete_queryset_for_user(user)
        group_qs = get_group_queryset_for_user(user)
        classes_qs = get_class_queryset_for_user(user)

        context.update(
            family_count=len(user.get_accessible_family_ids()) if not user.is_staff else Family.objects.count(),
            person_count=person_qs.count(),
            athlete_count=athlete_qs.count(),
            group_count=group_qs.count(),
            upcoming_classes=classes_qs.select_related("group").order_by("date")[:5],
        )
        return context


class AthleteListView(ApprovedUserRequiredMixin, ListView):
    model = Athlete

    def get_queryset(self):
        return (
            get_athlete_queryset_for_user(self.request.user)
            .select_related("person")
            .prefetch_related("groups_athletes")
            .order_by("person__surname", "person__name")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["level_choices"] = Athlete.LEVEL_CHOICES
        context["groups"] = (
            get_group_queryset_for_user(user)
            .prefetch_related("athletes")
            .order_by("name")
        )
        return context


class AthleteSimpleListView(ApprovedUserRequiredMixin, ListView):
    """Новый упрощённый список спортсменов (3 колонки, без инлайн-редактирования)."""

    template_name = "school/athlete_simple_list.html"
    model = Athlete

    def get_queryset(self):
        queryset = (
            get_athlete_queryset_for_user(self.request.user)
            .select_related("person", "person__club")
            .prefetch_related(
                Prefetch("groups_athletes", queryset=Group.objects.order_by("name"))
            )
        )
        club_id = self.request.GET.get("club")
        if club_id:
            queryset = queryset.filter(person__club_id=club_id)

        order = self.request.GET.get("order")
        if order == "dob":
            return queryset.order_by("person__date_of_birth", "person__surname", "person__name")
        if order == "group":
            # Сортируем по названию группы; distinct чтобы избежать дубликатов из-за M2M
            return queryset.order_by("groups_athletes__name", "person__surname", "person__name").distinct()
        if order == "club":
            return queryset.order_by("person__club__name", "person__surname", "person__name")
        return queryset.order_by("person__surname", "person__name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["order"] = self.request.GET.get("order") or ""
        context["clubs"] = Club.objects.order_by("name")
        context["selected_club"] = self.request.GET.get("club") or ""
        context["can_use_extended_filters"] = _is_extended_athlete_filters_user(self.request.user)
        order = context["order"]
        grouped = []

        if order == "dob":
            buckets = {}
            for athlete in context["object_list"]:
                year = None
                if athlete.person and athlete.person.date_of_birth:
                    year = athlete.person.date_of_birth.year
                label = year
                buckets.setdefault(label, []).append(athlete)
            grouped = sorted(
                ((("Без даты" if k is None else str(k)), v) for k, v in buckets.items()),
                key=lambda x: (x[0] == "Без даты", x[0]),
            )
        elif order == "group":
            buckets = {}
            for athlete in context["object_list"]:
                main_group = athlete.groups_athletes.first()
                label = main_group.name if main_group else "Без группы"
                buckets.setdefault(label, []).append(athlete)
            grouped = sorted(buckets.items(), key=lambda x: (x[0] == "Без группы", x[0]))
        elif order == "club":
            buckets = {}
            for athlete in context["object_list"]:
                club_name = athlete.person.club.name if athlete.person and athlete.person.club else "Без клуба"
                buckets.setdefault(club_name, []).append(athlete)
            grouped = sorted(buckets.items(), key=lambda x: (x[0] == "Без клуба", x[0]))

        context["grouped"] = grouped
        return context


# def edit_athlete(request, athlete_id):
#     athlete = get_object_or_404(Athlete, id=athlete_id)
#     person = athlete.person  # Получаем связанные данные из модели Person
#
#     if request.method == "POST":
#         athlete_form = AthleteForm(request.POST, instance=athlete)
#         person_form = PersonForm(request.POST, instance=person)
#         if athlete_form.is_valid() and person_form.is_valid():
#             athlete_form.save()
#             person_form.save()
#     else:
#         athlete_form = AthleteForm(instance=athlete)
#         person_form = PersonForm(instance=person)
#
#     return render(request, 'athletes/edit_form.html', {
#         'athlete_form': athlete_form,
#         'person_form': person_form,
#         'athlete': athlete,
#     })


@login_required
def edit_athlete(request, athlete_id):
    athlete = get_object_or_404(Athlete, id=athlete_id)

    if not request.user.is_staff and not request.user.is_superuser:
        allowed = get_athlete_queryset_for_user(request.user)
        if not allowed.filter(id=athlete.id).exists():
            if request.user.is_approved:
                return redirect("school:athlete_list")
            return redirect("users:awaiting_approval")

    person = athlete.person

    # Ищем семьи, к которым относится этот спортсмен
    family_memberships = FamilyMember.objects.filter(person=person)
    families = [membership.family for membership in family_memberships]

    # В случае нескольких семей, будем использовать первую (в зависимости от логики приложения)
    family = families[0] if families else None
    family_members = FamilyMember.objects.filter(family=family) if family else []

    if request.method == "POST":
        athlete_form = AthleteForm(request.POST, instance=athlete)
        person_form = PersonForm(request.POST, instance=person)
        family_form = FamilyForm(request.POST, instance=family) if family else None
        family_member_forms = [
            FamilyMemberForm(request.POST, prefix=str(member.id), instance=member)
            for member in family_members
        ]

        if (
            athlete_form.is_valid() and
            person_form.is_valid() and
            (family_form is None or family_form.is_valid()) and
            all(form.is_valid() for form in family_member_forms)
        ):
            athlete_form.save()
            person_form.save()
            if family_form:
                family_form.save()
            for form in family_member_forms:
                form.save()

    else:
        athlete_form = AthleteForm(instance=athlete)
        person_form = PersonForm(instance=person)
        family_form = FamilyForm(instance=family) if family else None
        family_member_forms = [FamilyMemberForm(prefix=str(member.id), instance=member) for member in family_members]

    return render(
        request,
        "athletes/edit_form.html",
        {
            "athlete_form": athlete_form,
            "person_form": person_form,
            "family_form": family_form,
            "family_member_forms": family_member_forms,
            "athlete": athlete,
        },
    )


class AthleteCompactEditView(ApprovedUserRequiredMixin, View):
    """Мобильный компактный экран редактирования спортсмена."""

    template_name = "athletes/edit_compact.html"
    modal_template_name = "athletes/edit_compact_modal_body.html"

    def get_athlete(self, request, pk):
        athlete = get_object_or_404(Athlete, pk=pk)
        if request.user.is_staff or request.user.is_superuser:
            return athlete
        allowed = get_available_athlete_queryset_for_user(request.user, ensure_family_links=True)
        if allowed.filter(id=athlete.id).exists():
            return athlete
        if request.user.is_approved:
            return None
        return None

    @staticmethod
    def _is_modal(request) -> bool:
        return request.GET.get("modal") == "1" or request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def _build_context(self, request, athlete, person_form, athlete_form, *, saved: bool):
        certificate_ctx = _build_athlete_certificate_context(athlete)
        cert_ui = _resolve_certificate_ui_state(certificate_ctx["active_document"])
        club_name = (athlete.person.club.name if athlete.person and athlete.person.club else "") or ""
        can_show_group = club_name.strip().lower() == KANAEV_CLUB_NAME.lower()
        return {
            "athlete": athlete,
            "person_form": person_form,
            "athlete_form": athlete_form,
            "saved": saved,
            "can_show_group": can_show_group,
            "can_manage_certificates": _can_manage_athlete_certificates(request.user),
            **certificate_ctx,
            **cert_ui,
        }

    def get(self, request, pk):
        athlete = self.get_athlete(request, pk)
        if athlete is None:
            return redirect("school:athlete_list")
        person = athlete.person
        group_qs = get_group_queryset_for_user(request.user)
        person_form = PersonCompactForm(instance=person)
        athlete_form = AthleteCompactForm(instance=athlete, group_qs=group_qs)
        context = self._build_context(request, athlete, person_form, athlete_form, saved=False)
        if self._is_modal(request):
            html = render_to_string(self.modal_template_name, context=context, request=request)
            return JsonResponse({"success": True, "html": html})
        return render(request, self.template_name, context)

    def post(self, request, pk):
        athlete = self.get_athlete(request, pk)
        if athlete is None:
            return redirect("school:athlete_list")
        person = athlete.person
        group_qs = get_group_queryset_for_user(request.user)
        person_form = PersonCompactForm(request.POST, request.FILES, instance=person)
        athlete_form = AthleteCompactForm(request.POST, instance=athlete, group_qs=group_qs)

        if person_form.is_valid() and athlete_form.is_valid():
            person_form.save()
            athlete_form.save()
            saved = True
        else:
            saved = False

        context = self._build_context(request, athlete, person_form, athlete_form, saved=saved)
        if self._is_modal(request):
            html = render_to_string(self.modal_template_name, context=context, request=request)
            return JsonResponse(
                {
                    "success": saved,
                    "html": html,
                    "message": "Сохранено" if saved else "Проверьте заполнение полей",
                },
                status=200 if saved else 400,
            )
        return render(request, self.template_name, context)


class AthleteInlineUpdateView(ApprovedUserRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        athlete = (
            get_athlete_queryset_for_user(request.user)
            .select_related("person")
            .prefetch_related("groups_athletes")
            .filter(pk=pk)
            .first()
        )
        if not athlete:
            return JsonResponse({"error": "not_found"}, status=404)

        try:
            payload = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "invalid_payload"}, status=400)

        person = athlete.person
        updated_fields: list[str] = []
        response_payload: dict[str, object] = {}

        birth_year = payload.get("birth_year")
        if birth_year is not None and person:
            try:
                year = int(birth_year)
            except (TypeError, ValueError):
                return JsonResponse({"error": "invalid_birth_year"}, status=400)
            current_year = timezone.now().year
            if year < 1900 or year > current_year:
                return JsonResponse({"error": "invalid_birth_year"}, status=400)
            if person.date_of_birth:
                month = person.date_of_birth.month
                day = person.date_of_birth.day
                try:
                    new_date = person.date_of_birth.replace(year=year)
                except ValueError:
                    if month == 2 and day == 29:
                        new_date = person.date_of_birth.replace(year=year, month=2, day=28)
                    else:
                        return JsonResponse({"error": "invalid_birth_year"}, status=400)
            else:
                new_date = date(year, 1, 1)
            if person.date_of_birth != new_date:
                person.date_of_birth = new_date
                person.save(update_fields=["date_of_birth"])
            response_payload["birth_year"] = year
        elif person and person.date_of_birth:
            response_payload["birth_year"] = person.date_of_birth.year
        else:
            response_payload["birth_year"] = None

        level = payload.get("level")
        if level is not None:
            valid_levels = {choice for choice, _ in Athlete.LEVEL_CHOICES}
            if level not in valid_levels:
                return JsonResponse({"error": "invalid_level"}, status=400)
            if athlete.level != level:
                athlete.level = level
                updated_fields.append("level")

        text_fields = {
            "rank": "rank",
            "medical_certificate": "medical_certificate",
            "comment": "comment",
        }
        for key, field in text_fields.items():
            if key in payload:
                value = payload.get(key) or ""
                normalized = value.strip()
                if getattr(athlete, field) != normalized:
                    setattr(athlete, field, normalized)
                    updated_fields.append(field)

        group_ids = payload.get("group_ids")
        if group_ids is not None:
            try:
                group_ids = [int(group_id) for group_id in group_ids]
            except (TypeError, ValueError):
                return JsonResponse({"error": "invalid_group_ids"}, status=400)
            allowed_group_ids = set(
                get_group_queryset_for_user(request.user).values_list("id", flat=True)
            )
            cleaned_group_ids = [gid for gid in group_ids if gid in allowed_group_ids]
            athlete.groups_athletes.set(cleaned_group_ids)

        if updated_fields:
            athlete.save(update_fields=updated_fields)

        refreshed_groups = list(
            Group.objects.filter(
                id__in=athlete.groups_athletes.values_list("id", flat=True)
            )
            .order_by("name")
            .values("id", "name")
        )

        response_payload.update(
            {
                "level": athlete.level,
                "rank": athlete.rank or "",
                "medical_certificate": athlete.medical_certificate or "",
                "comment": athlete.comment or "",
                "groups": refreshed_groups,
            }
        )
        return JsonResponse({"success": True, "athlete": response_payload})


class AthleteCertificateUploadView(ApprovedUserRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        athlete = get_athlete_queryset_for_user(request.user).select_related("person").filter(pk=pk).first()
        if not athlete:
            return JsonResponse({"error": "not_found"}, status=404)
        if not _can_manage_athlete_certificates(request.user):
            return JsonResponse({"error": "forbidden"}, status=403)

        uploaded = request.FILES.get("certificate")
        if not uploaded:
            return JsonResponse({"error": "empty_file"}, status=400)

        try:
            ingest_result = ingest_single_document(
                file_obj=uploaded,
                original_name=getattr(uploaded, "name", "") or "",
                mime_type=getattr(uploaded, "content_type", "") or "",
                size=int(getattr(uploaded, "size", 0) or 0),
                uploaded_by=request.user,
                source="web_athlete_certificate",
                source_meta={"athlete_id": athlete.id, "uploaded_via": "athlete_modal"},
                enqueue_analysis=True,
                deduplicate=True,
                enqueue_existing=True,
                allowed_extensions={"pdf", "jpg", "jpeg", "png", "webp", "heic", "heif"},
                max_size_mb=20,
            )
        except DocumentIngestValidationError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

        document = Document.objects.filter(pk=ingest_result.document_id).first()
        if not document:
            return JsonResponse({"error": "document_not_found"}, status=400)

        with transaction.atomic():
            link, created = AthleteDocument.objects.get_or_create(
                athlete=athlete,
                document=document,
                defaults={
                    "doc_type": DocumentType.MED_CERT,
                    "is_actual": False,
                },
            )
            if not created and link.doc_type != DocumentType.MED_CERT:
                link.doc_type = DocumentType.MED_CERT
                link.save(update_fields=["doc_type"])

            AthleteDocument.objects.filter(
                athlete=athlete,
                doc_type=DocumentType.MED_CERT,
            ).exclude(pk=link.pk).update(is_actual=False)
            link.is_actual = True
            link.save(update_fields=["is_actual"])

        monitor_athlete_document_analysis_status.delay(athlete_document_id=link.id, timeout_seconds=60)

        certificate_ctx = _build_athlete_certificate_context(athlete)
        return JsonResponse(
            {
                "success": True,
                "message": "Файл загружен и отправлен на обработку",
                "athlete_document_id": link.id,
                "processing_seconds": 60,
                "certificate": certificate_ctx["active_payload"],
            }
        )


class AthleteCertificateStatusView(ApprovedUserRequiredMixin, View):
    http_method_names = ["get"]

    def get(self, request, pk):
        athlete = get_athlete_queryset_for_user(request.user).select_related("person").filter(pk=pk).first()
        if not athlete:
            return JsonResponse({"error": "not_found"}, status=404)

        certificate_ctx = _build_athlete_certificate_context(athlete)
        cert_ui = _resolve_certificate_ui_state(certificate_ctx["active_document"])
        active_payload = certificate_ctx["active_payload"]
        label = cert_ui["badge_text"]

        doc_id_raw = request.GET.get("athlete_document_id")
        processing = None
        if doc_id_raw:
            try:
                doc_id_int = int(doc_id_raw)
            except (TypeError, ValueError):
                doc_id_int = None
            if doc_id_int:
                link = (
                    AthleteDocument.objects.select_related("document__ai_analysis")
                    .filter(pk=doc_id_int, athlete=athlete)
                    .first()
                )
                if link:
                    analysis = getattr(link.document, "ai_analysis", None)
                    if not analysis or analysis.status == DocumentAIAnalysis.Status.PENDING:
                        processing = {"state": "processing", "message": "анализируем документ"}
                    elif analysis.is_analyzed_successfully:
                        link_ui = _resolve_certificate_ui_state(link)
                        if link_ui["is_unrecognized"]:
                            processing = {"state": "unrecognized", "message": "Справка не распознана"}
                        elif link_ui["show_clarify"]:
                            processing = {"state": "need_clarification", "message": "Укажите дату вручную"}
                        else:
                            processing = {"state": "done", "message": "Справка распознана"}
                    elif analysis.status in {
                        DocumentAIAnalysis.Status.FAILED_DOWNLOAD,
                        DocumentAIAnalysis.Status.FAILED_OPENAI,
                        DocumentAIAnalysis.Status.FAILED_VALIDATION,
                        DocumentAIAnalysis.Status.ERROR,
                    }:
                        processing = {
                            "state": "failed",
                            "message": analysis.error_message or "Ошибка обработки документа",
                        }
                if processing is None:
                    processing = cache.get(_monitor_cache_key(doc_id_int))

        return JsonResponse(
            {
                "success": True,
                "label": label,
                "has_active": bool(active_payload and active_payload.get("valid_until")),
                "needs_clarification": bool(cert_ui["show_clarify"]),
                "is_unrecognized": bool(cert_ui["is_unrecognized"]),
                "badge_tone": cert_ui["badge_tone"],
                "active_certificate": active_payload,
                "inactive_certificates": certificate_ctx["inactive_payload"],
                "processing": processing,
            }
        )


class AthleteCertificateClarifyView(ApprovedUserRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk, athlete_document_id):
        athlete = get_athlete_queryset_for_user(request.user).filter(pk=pk).first()
        if not athlete:
            return JsonResponse({"error": "not_found"}, status=404)
        if not _can_manage_athlete_certificates(request.user):
            return JsonResponse({"error": "forbidden"}, status=403)

        link = AthleteDocument.objects.select_related("athlete").filter(
            pk=athlete_document_id,
            athlete=athlete,
            doc_type=DocumentType.MED_CERT,
        ).first()
        if not link:
            return JsonResponse({"error": "document_not_found"}, status=404)

        valid_until_raw = (request.POST.get("valid_until") or "").strip()
        try:
            valid_until = date.fromisoformat(valid_until_raw)
        except ValueError:
            return JsonResponse({"error": "invalid_valid_until"}, status=400)

        link.valid_until = valid_until
        link.needs_valid_until_clarification = False
        link.is_actual = valid_until >= timezone.localdate()
        link.save(update_fields=["valid_until", "needs_valid_until_clarification", "is_actual"])

        if link.is_actual:
            AthleteDocument.objects.filter(
                athlete=athlete,
                doc_type=DocumentType.MED_CERT,
            ).exclude(pk=link.pk).update(is_actual=False)

        certificate_ctx = _build_athlete_certificate_context(athlete)
        cert_ui = _resolve_certificate_ui_state(certificate_ctx["active_document"])
        return JsonResponse(
            {
                "success": True,
                "message": "Срок действия обновлён",
                "label": cert_ui["badge_text"],
                "badge_tone": cert_ui["badge_tone"],
                "needs_clarification": cert_ui["show_clarify"],
                "is_unrecognized": cert_ui["is_unrecognized"],
                "active_certificate": certificate_ctx["active_payload"],
                "inactive_certificates": certificate_ctx["inactive_payload"],
            }
        )


class CompetitionListView(ApprovedUserRequiredMixin, ListView):
    model = Competition
    template_name = "competitions/competition_list.html"

    def get_queryset(self):
        return (
            Competition.objects.prefetch_related("entries__athlete__person", "documents__document")
            .select_related("application_link", "location", "location__parent")
            .order_by(Coalesce("start_date", "date").desc(nulls_last=True), "-name")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["competition_form"] = CompetitionForm()
        return ctx


def _issue_competition_link_token() -> str:
    while True:
        token = secrets.token_urlsafe(32)
        if not CompetitionApplicationLink.objects.filter(token=token).exists():
            return token


def _get_or_create_competition_application_link(
    competition: Competition,
    *,
    expires_at=None,
) -> CompetitionApplicationLink:
    link = CompetitionApplicationLink.objects.filter(competition=competition).first()
    if link:
        if link.expires_at != expires_at:
            link.expires_at = expires_at
            link.save(update_fields=["expires_at", "updated_at"])
        return link
    return CompetitionApplicationLink.objects.create(
        competition=competition,
        token=_issue_competition_link_token(),
        expires_at=expires_at,
    )


def _get_visible_competition_entries(user, competition: Competition, available_athlete_ids: set[int]):
    base_qs = competition.entries.select_related(
        "athlete__person",
        "athlete__person__club",
    )
    if user.is_staff or user.is_superuser:
        return base_qs

    coach_club_ids = get_coach_club_ids_for_user(user)
    if coach_club_ids:
        return base_qs.filter(athlete__person__club_id__in=coach_club_ids)

    if available_athlete_ids:
        return base_qs.filter(athlete_id__in=available_athlete_ids)
    return base_qs.none()


def _athlete_filter_queryset(user, club_id=None, year_from=None, year_to=None, order="surname"):
    qs = (
        get_available_athlete_queryset_for_user(user, ensure_family_links=True)
        .select_related("person__club")
        .prefetch_related(Prefetch("groups_athletes", queryset=Group.objects.order_by("name")))
    )
    if club_id:
        qs = qs.filter(person__club_id=club_id)
    if year_from:
        qs = qs.filter(person__date_of_birth__year__gte=year_from)
    if year_to:
        qs = qs.filter(person__date_of_birth__year__lte=year_to)
    if order == "dob":
        qs = qs.order_by("person__date_of_birth", "person__surname", "person__name")
    else:
        qs = qs.order_by("person__surname", "person__name")
    return qs


class CompetitionCreateUpdateView(ApprovedUserRequiredMixin, View):
    template_name = "competitions/competition_form.html"

    def get_object(self, pk):
        if pk is None:
            return None
        return get_object_or_404(Competition, pk=pk)

    def get(self, request, pk=None):
        competition = self.get_object(pk)
        link = None
        initial = {}
        if competition:
            link = CompetitionApplicationLink.objects.filter(competition=competition).first()
            if link and link.expires_at:
                initial["application_deadline"] = link.expires_at
        form = CompetitionForm(instance=competition, initial=initial)
        document_form = CompetitionDocumentUploadForm()
        club_id = request.GET.get("club") or ""
        year_from = request.GET.get("year_from") or ""
        year_to = request.GET.get("year_to") or ""
        order = request.GET.get("order") or "surname"
        athletes = _athlete_filter_queryset(
            request.user,
            club_id=None,
            year_from=None,
            year_to=None,
            order="surname",
        )
        selected_ids = set()
        if competition:
            selected_ids = set(competition.entries.values_list("athlete_id", flat=True))
            if not link:
                link = _get_or_create_competition_application_link(competition)
        context = {
            "form": form,
            "competition": competition,
            "athletes": athletes,
            "clubs": Club.objects.order_by("name"),
            "selected_club": club_id,
            "year_from": year_from,
            "year_to": year_to,
            "order": order,
            "selected_ids": selected_ids,
            "application_link": link,
            "apply_url": request.build_absolute_uri(
                reverse("school:competition_apply", kwargs={"pk": competition.pk, "token": link.token})
            ) if competition and link else "",
            "competition_documents": competition.documents.select_related("document") if competition else [],
            "document_form": document_form,
        }
        return render(request, self.template_name, context)


    def post(self, request, pk=None):
        competition = self.get_object(pk)
        form = CompetitionForm(request.POST, instance=competition)
        save_competition = "save_competition" in request.POST
        save_athletes = "athletes_submit" in request.POST
        selected_ids = set(request.POST.getlist("athletes"))

        # Если просто сохраняем участников и соревнование уже существует — не требуем валидности формы
        if save_athletes and competition:
            selected_ids_int = [int(aid) for aid in selected_ids]
            existing = set(competition.entries.values_list("athlete_id", flat=True))
            competition.entries.exclude(athlete_id__in=selected_ids_int).delete()
            to_add = [aid for aid in selected_ids_int if aid not in existing]
            CompetitionEntry.objects.bulk_create(
                [CompetitionEntry(competition=competition, athlete_id=aid) for aid in to_add]
            )
            return redirect("school:competition_edit", pk=competition.pk)

        if form.is_valid():
            competition = form.save(commit=False)
            if competition.start_date is None and competition.date:
                competition.start_date = competition.date
            if competition.start_date is None and form.cleaned_data.get("start_date"):
                competition.start_date = form.cleaned_data["start_date"]
            competition.save()
            _get_or_create_competition_application_link(
                competition,
                expires_at=form.cleaned_data.get("application_deadline"),
            )
            if save_athletes:
                selected_ids_int = [int(aid) for aid in selected_ids]
                existing = set(competition.entries.values_list("athlete_id", flat=True))
                competition.entries.exclude(athlete_id__in=selected_ids_int).delete()
                to_add = [aid for aid in selected_ids_int if aid not in existing]
                CompetitionEntry.objects.bulk_create(
                    [CompetitionEntry(competition=competition, athlete_id=aid) for aid in to_add]
                )
            if save_athletes or save_competition:
                return redirect("school:competition_edit", pk=competition.pk)

        # invalid form or no save flag: re-render with all athletes
        athletes = _athlete_filter_queryset(
            request.user,
            club_id=None,
            year_from=None,
            year_to=None,
            order="surname",
        )
        link = CompetitionApplicationLink.objects.filter(competition=competition).first() if competition else None
        document_form = CompetitionDocumentUploadForm()
        context = {
            "form": form,
            "competition": competition,
            "athletes": athletes,
            "clubs": Club.objects.order_by("name"),
            "selected_club": "",
            "year_from": "",
            "year_to": "",
            "order": "surname",
            "selected_ids": selected_ids,
            "application_link": link,
            "apply_url": request.build_absolute_uri(
                reverse("school:competition_apply", kwargs={"pk": competition.pk, "token": link.token})
            ) if competition and link else "",
            "competition_documents": competition.documents.select_related("document") if competition else [],
            "document_form": document_form,
        }
        return render(request, self.template_name, context)


class CompetitionVenueListCreateView(ApprovedUserRequiredMixin, ListView):
    model = CompetitionVenue
    template_name = "competitions/venue_list.html"
    context_object_name = "venues"

    def get_queryset(self):
        return CompetitionVenue.objects.select_related("parent").order_by("parent__short_name", "short_name")

    def post(self, request, *args, **kwargs):
        form = CompetitionVenueForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Локация сохранена.")
            return redirect("school:competition_venue_list")

        context = self.get_context_data()
        context["create_form"] = form
        return render(request, self.template_name, context)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.setdefault("create_form", CompetitionVenueForm())
        return context


class CompetitionVenueUpdateView(ApprovedUserRequiredMixin, View):
    template_name = "competitions/venue_form.html"

    def get_object(self, pk: int) -> CompetitionVenue:
        return get_object_or_404(CompetitionVenue.objects.select_related("parent"), pk=pk)

    def get(self, request, pk):
        venue = self.get_object(pk)
        form = CompetitionVenueForm(instance=venue)
        return render(request, self.template_name, {"form": form, "venue": venue})

    def post(self, request, pk):
        venue = self.get_object(pk)
        form = CompetitionVenueForm(request.POST, instance=venue)
        if form.is_valid():
            form.save()
            messages.success(request, "Локация обновлена.")
            return redirect("school:competition_venue_list")
        return render(request, self.template_name, {"form": form, "venue": venue})


class CompetitionVenueRecheckUnboundDocumentsView(ApprovedUserRequiredMixin, View):
    def post(self, request):
        rebind_unbound_competition_documents_task.delay()
        messages.success(request, "Перепроверка непривязанных документов поставлена в очередь.")
        return redirect("school:competition_venue_list")


class CompetitionDocumentUploadView(ApprovedUserRequiredMixin, View):
    """Загрузка документов соревнования (общих)."""

    def post(self, request, pk):
        competition = get_object_or_404(Competition, pk=pk)
        form = CompetitionDocumentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            doc = form.save(competition=competition, user=request.user)
            enqueue_documents_for_ai_analysis.delay(document_ids=[doc.id])
            messages.success(request, "Документ загружен.")
        else:
            messages.error(request, "Не удалось загрузить документ. Проверьте форму.")
        return redirect("school:competition_edit", pk=pk)


class CompetitionDocumentDeleteView(ApprovedUserRequiredMixin, View):
    """Удаление документа соревнования."""

    def post(self, request, pk, doc_id):
        competition = get_object_or_404(Competition, pk=pk)
        link = get_object_or_404(CompetitionDocument, pk=doc_id, competition=competition)
        doc = link.document
        link.delete()
        # удаляем сам файл, если больше не используется
        if not doc.competition_links.exists() and not doc.athlete_links.exists():
            doc.delete()
        messages.success(request, "Документ удалён.")
        return redirect("school:competition_edit", pk=pk)


class SignedDocumentAccessView(View):
    """Короткоживущий доступ к документу по signed URL (для AI сервиса)."""

    def get(self, request, document_id):
        token = request.GET.get("token")
        if not token:
            raise Http404("Missing signed token")

        document = get_object_or_404(Document, pk=document_id)
        max_age = int(getattr(settings, "DOCS_ANALYZER_SIGNED_URL_TTL_SEC", 300))
        try:
            _, signed_updated_ts, _ = validate_signed_document_token(
                document_id=document_id,
                token=token,
                max_age=max_age,
            )
        except PermissionError as exc:
            raise Http404(str(exc)) from exc

        current_updated_ts = int(document.updated_at.timestamp()) if document.updated_at else 0
        if signed_updated_ts != current_updated_ts:
            raise Http404("Document version has changed")

        file_obj = document.file.open(mode="rb")
        response = FileResponse(
            file_obj,
            as_attachment=False,
            filename=document.original_name or document.file.name.rsplit("/", 1)[-1],
            content_type=document.mime_type or "application/octet-stream",
        )
        response["Cache-Control"] = "no-store"
        return response


class BulkDocumentUploadView(ApprovedUserRequiredMixin, View):
    template_name = "school/document_bulk_upload.html"

    def _get_form(self, request, *, data=None, files=None):
        return BulkDocumentUploadForm(data=data, files=files)

    @staticmethod
    def _chunk_size():
        return int(getattr(settings, "DOCS_BULK_UPLOAD_CHUNK_SIZE", 20))

    def get(self, request):
        form = self._get_form(request)
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "bulk_upload_chunk_size": self._chunk_size(),
            },
        )

    def post(self, request):
        form = self._get_form(request, data=request.POST, files=request.FILES)
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if not form.is_valid():
            if is_ajax:
                return JsonResponse(
                    {
                        "ok": False,
                        "errors": form.errors.get_json_data(),
                        "non_field_errors": form.non_field_errors(),
                    },
                    status=400,
                )
            messages.error(request, "Не удалось выполнить массовую загрузку. Проверьте форму.")
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "bulk_upload_chunk_size": self._chunk_size(),
                },
            )

        save_result = form.save(user=request.user)
        document_ids = save_result["document_ids"]
        created_ids = save_result.get("created_ids") or []
        # Auto-analysis is only for brand new uploads.
        enqueue_ids = list(dict.fromkeys(created_ids))
        if enqueue_ids:
            enqueue_documents_for_ai_analysis.delay(document_ids=enqueue_ids)
        if is_ajax:
            return JsonResponse(
                {
                    "ok": True,
                    "uploaded": save_result["total_count"],
                    "created": save_result["created_count"],
                    "reused": save_result["reused_count"],
                    "document_ids": document_ids,
                    "queued_document_ids": enqueue_ids,
                }
            )
        messages.success(
            request,
            (
                "Обработано документов: "
                f"{save_result['total_count']} "
                f"(новых: {save_result['created_count']}, дублей: {save_result['reused_count']}). "
                "Анализ поставлен в очередь."
            ),
        )
        return redirect("school:documents_bulk_upload")


class DocumentAIAnalysisListView(ApprovedUserRequiredMixin, ListView):
    model = Document
    template_name = "school/document_ai_analysis_list.html"
    context_object_name = "items"
    paginate_by = 50

    def get_filter_form(self):
        return DocumentAIAnalysisFilterForm(self.request.GET or None)

    def post(self, request, *args, **kwargs):
        document_id = request.POST.get("document_id")
        force = request.POST.get("force") == "1"
        try:
            document_id_int = int(document_id)
        except (TypeError, ValueError):
            messages.error(request, "Некорректный ID документа.")
            return redirect("school:documents_analysis_list")

        if not Document.objects.filter(pk=document_id_int).exists():
            messages.error(request, "Документ не найден.")
            return redirect("school:documents_analysis_list")

        enqueue_documents_for_ai_analysis.delay(document_ids=[document_id_int], force=force)
        if force:
            messages.success(request, f"Документ #{document_id_int} поставлен на переанализ.")
        else:
            messages.success(request, f"Документ #{document_id_int} поставлен в анализ.")
        return redirect("school:documents_analysis_list")

    def get_queryset(self):
        queryset = (
            Document.objects.select_related("ai_analysis")
            .prefetch_related(
                "competition_links__competition",
                "athlete_links__athlete__person",
            )
            .order_by("-ai_analysis__analyzed_at", "-updated_at")
        )

        form = self.get_filter_form()
        if not form.is_valid():
            return queryset

        status = form.cleaned_data.get("status")
        doc_type = form.cleaned_data.get("doc_type")
        entity = form.cleaned_data.get("entity")
        date_from = form.cleaned_data.get("date_from")
        date_to = form.cleaned_data.get("date_to")

        if status:
            if status == "not_analyzed":
                queryset = queryset.filter(ai_analysis__isnull=True)
            else:
                queryset = queryset.filter(ai_analysis__status=status)
        if doc_type:
            queryset = queryset.filter(ai_analysis__doc_type=doc_type)
        if date_from:
            queryset = queryset.filter(ai_analysis__analyzed_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(ai_analysis__analyzed_at__date__lte=date_to)

        if entity == "competition":
            queryset = queryset.filter(
                competition_links__isnull=False,
                athlete_links__isnull=True,
            )
        elif entity == "athlete":
            queryset = queryset.filter(
                competition_links__isnull=True,
                athlete_links__isnull=False,
            )
        elif entity == "mixed":
            queryset = queryset.filter(
                competition_links__isnull=False,
                athlete_links__isnull=False,
            )
        elif entity == "other":
            queryset = queryset.filter(
                competition_links__isnull=True,
                athlete_links__isnull=True,
            )

        return queryset.distinct()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.get_filter_form()
        context["gender_choices"] = Person.GENDER_CHOICES
        return context


class DocumentBindingContextView(ApprovedUserRequiredMixin, View):
    def get(self, request, document_id):
        document = get_object_or_404(Document.objects.select_related("ai_analysis"), pk=document_id)
        context = get_binding_context(document)
        return JsonResponse(context)


class DocumentBindCompetitionView(ApprovedUserRequiredMixin, View):
    def post(self, request, document_id):
        document = get_object_or_404(Document, pk=document_id)
        competition = get_object_or_404(Competition, pk=request.POST.get("competition_id"))
        doc_type = request.POST.get("doc_type") or DocumentType.OTHER
        title = request.POST.get("title") or ""

        if doc_type not in {value for value, _ in DocumentType.choices}:
            messages.error(request, "Некорректный тип документа.")
            return redirect("school:documents_analysis_list")

        with transaction.atomic():
            link, created = CompetitionDocument.objects.get_or_create(
                competition=competition,
                document=document,
                defaults={
                    "doc_type": doc_type,
                    "title": title,
                    "is_public": False,
                },
            )
            if not created:
                link.doc_type = doc_type
                if title:
                    link.title = title
                link.save(update_fields=["doc_type", "title"])
            DocumentAIAnalysis.objects.filter(document=document).update(
                auto_bound=False,
                bound_entity_type="competition",
                bound_entity_id=competition.id,
                bound_at=timezone.now(),
            )

        messages.success(request, "Документ привязан к соревнованию.")
        return redirect("school:documents_analysis_list")


class DocumentBindAthleteView(ApprovedUserRequiredMixin, View):
    def post(self, request, document_id):
        document = get_object_or_404(Document, pk=document_id)
        athlete = get_object_or_404(Athlete, pk=request.POST.get("athlete_id"))
        doc_type = request.POST.get("doc_type") or DocumentType.OTHER

        if doc_type not in {value for value, _ in DocumentType.choices}:
            messages.error(request, "Некорректный тип документа.")
            return redirect("school:documents_analysis_list")

        with transaction.atomic():
            link, created = AthleteDocument.objects.get_or_create(
                athlete=athlete,
                document=document,
                defaults={"doc_type": doc_type},
            )
            if not created:
                link.doc_type = doc_type
                link.save(update_fields=["doc_type"])
            sync_athlete_document_from_analysis(document)
            if doc_type == DocumentType.MED_CERT:
                AthleteDocument.objects.filter(
                    athlete=athlete,
                    doc_type=DocumentType.MED_CERT,
                ).exclude(pk=link.pk).update(is_actual=False)
                link.refresh_from_db()
                if not link.is_actual:
                    link.is_actual = True
                    link.save(update_fields=["is_actual"])
            DocumentAIAnalysis.objects.filter(document=document).update(
                auto_bound=False,
                bound_entity_type="athlete",
                bound_entity_id=athlete.id,
                bound_at=timezone.now(),
            )

        messages.success(request, "Документ привязан к спортсмену.")
        return redirect("school:documents_analysis_list")


class DocumentCreateCompetitionFromAIView(ApprovedUserRequiredMixin, View):
    def post(self, request, document_id):
        document = get_object_or_404(Document.objects.select_related("ai_analysis"), pk=document_id)
        if not getattr(document, "ai_analysis", None):
            messages.error(request, "Для документа нет AI-анализа.")
            return redirect("school:documents_analysis_list")

        with transaction.atomic():
            competition = create_competition_from_analysis(document)
            doc_type = request.POST.get("doc_type") or DocumentType.OTHER
            bind_document_to_competition(
                document=document,
                competition=competition,
                doc_type=doc_type,
                title=document.ai_analysis.title or "",
            )
            DocumentAIAnalysis.objects.filter(document=document).update(
                auto_bound=False,
                bound_entity_type="competition",
                bound_entity_id=competition.id,
                bound_at=timezone.now(),
            )

        messages.success(request, "Создано новое соревнование и выполнена привязка документа.")
        return redirect("school:documents_analysis_list")


class DocumentCreateAthleteFromAIView(ApprovedUserRequiredMixin, View):
    def post(self, request, document_id):
        document = get_object_or_404(Document.objects.select_related("ai_analysis"), pk=document_id)
        if not getattr(document, "ai_analysis", None):
            messages.error(request, "Для документа нет AI-анализа.")
            return redirect("school:documents_analysis_list")

        gender = request.POST.get("gender")
        allowed_genders = {choice[0] for choice in Person.GENDER_CHOICES}
        if gender not in allowed_genders:
            messages.error(request, "Выберите пол для создания спортсмена.")
            return redirect("school:documents_analysis_list")

        with transaction.atomic():
            athlete = create_athlete_from_analysis(document=document, gender=gender)
            doc_type = request.POST.get("doc_type") or DocumentType.OTHER
            bind_document_to_athlete(
                document=document,
                athlete=athlete,
                doc_type=doc_type,
            )
            DocumentAIAnalysis.objects.filter(document=document).update(
                auto_bound=False,
                bound_entity_type="athlete",
                bound_entity_id=athlete.id,
                bound_at=timezone.now(),
            )

        messages.success(request, "Создан новый спортсмен и выполнена привязка документа.")
        return redirect("school:documents_analysis_list")


class CompetitionEntryToggleView(ApprovedUserRequiredMixin, View):
    """Add/remove athletes in a competition without page reload (staff form)."""

    def get_object(self, pk):
        return get_object_or_404(Competition, pk=pk)

    def post(self, request, pk):
        competition = self.get_object(pk)
        action = request.POST.get("action")
        athlete_id = request.POST.get("athlete_id")

        if not athlete_id or action not in {"add", "remove"}:
            return JsonResponse({"success": False, "error": "bad_request"}, status=400)

        try:
            athlete_id_int = int(athlete_id)
        except (TypeError, ValueError):
            return JsonResponse({"success": False, "error": "bad_athlete"}, status=400)

        available_ids = set(
            _athlete_filter_queryset(request.user).values_list("id", flat=True)
        )
        if athlete_id_int not in available_ids:
            return JsonResponse({"success": False, "error": "forbidden"}, status=403)

        if action == "add":
            CompetitionEntry.objects.get_or_create(
                competition=competition,
                athlete_id=athlete_id_int,
                defaults={"application": None},
            )
            return JsonResponse({"success": True, "status": "added"})

        # remove
        CompetitionEntry.objects.filter(
            competition=competition,
            athlete_id=athlete_id_int,
        ).delete()
        return JsonResponse({"success": True, "status": "removed"})

def _has_new_athlete_payload(data) -> bool:
    fields = [
        "surname",
        "name",
        "middlename",
        "date_of_birth",
    ]
    return any(data.get(field) for field in fields)


def _get_or_create_athlete_from_form(form: CompetitionApplyAthleteForm) -> Athlete:
    data = form.cleaned_data
    surname = data["surname"].strip()
    name = data["name"].strip()
    middlename = (data.get("middlename") or "").strip()
    dob = data["date_of_birth"]
    gender = data["gender"]
    club = data["club"]
    rank = data["rank"]

    person_qs = Person.objects.filter(
        surname__iexact=surname,
        name__iexact=name,
        date_of_birth=dob,
        gender=gender,
    )
    if middlename:
        person_qs = person_qs.filter(middlename__iexact=middlename)
    else:
        person_qs = person_qs.filter(Q(middlename__isnull=True) | Q(middlename=""))

    person = person_qs.first()
    if not person:
        person = Person.objects.create(
            surname=surname,
            name=name,
            middlename=middlename or None,
            date_of_birth=dob,
            gender=gender,
            club=club,
        )
    else:
        update_fields = []
        if not person.middlename and middlename:
            person.middlename = middlename
            update_fields.append("middlename")
        if club and person.club_id != club.id:
            person.club = club
            update_fields.append("club")
        if update_fields:
            person.save(update_fields=update_fields)

    athlete = Athlete.objects.filter(person=person).first()
    if not athlete:
        athlete = Athlete.objects.create(
            person=person,
            level=Athlete.LEVEL_CHOICES[0][0],
            rank=rank,
        )
    elif rank and athlete.rank != rank:
        athlete.rank = rank
        athlete.save(update_fields=["rank"])

    return athlete


class CompetitionApplyView(LoginRequiredMixin, View):
    template_name = "competitions/competition_apply.html"

    def _get_competition_link(self, competition: Competition, token: str) -> CompetitionApplicationLink:
        return get_object_or_404(
            CompetitionApplicationLink,
            competition=competition,
            token=token,
            is_active=True,
        )

    def _get_available_athletes(self, request):
        qs = get_available_athlete_queryset_for_user(request.user, ensure_family_links=True)
        return qs.select_related("person__club").prefetch_related(
            Prefetch("groups_athletes", queryset=Group.objects.order_by("name"))
        )

    def _build_context(self, request, competition, link, *, athlete_form=None, selected_ids=None):
        available_athletes = self._get_available_athletes(request)
        available_ids = set(available_athletes.values_list("id", flat=True))
        visible_entries = _get_visible_competition_entries(request.user, competition, available_ids)
        selected_ids = selected_ids or set(
            competition.entries.filter(athlete_id__in=available_ids).values_list("athlete_id", flat=True)
        )
        now = timezone.now()
        deadline = link.expires_at
        is_expired = bool(deadline and now > deadline)
        return {
            "competition": competition,
            "application_link": link,
            "apply_url": request.build_absolute_uri(
                reverse("school:competition_apply", kwargs={"pk": competition.pk, "token": link.token})
            ),
            "deadline": deadline,
            "is_expired": is_expired,
            "available_athletes": available_athletes,
            "visible_entries": visible_entries,
            "selected_ids": selected_ids,
            "athlete_form": athlete_form or CompetitionApplyAthleteForm(),
        }

    def get(self, request, pk, token):
        competition = get_object_or_404(Competition, pk=pk)
        link = self._get_competition_link(competition, token)
        context = self._build_context(request, competition, link)
        return render(request, self.template_name, context)

    def post(self, request, pk, token):
        competition = get_object_or_404(Competition, pk=pk)
        link = self._get_competition_link(competition, token)
        now = timezone.now()
        if link.expires_at and now > link.expires_at:
            messages.warning(request, "Срок подачи заявок истёк. Изменения недоступны.")
            context = self._build_context(request, competition, link)
            return render(request, self.template_name, context)

        available_athletes = self._get_available_athletes(request)
        available_ids = set(available_athletes.values_list("id", flat=True))
        selected_ids = {
            int(value)
            for value in request.POST.getlist("athletes")
            if str(value).isdigit()
        }

        athlete_form = CompetitionApplyAthleteForm(request.POST)
        if _has_new_athlete_payload(request.POST):
            if athlete_form.is_valid():
                athlete = _get_or_create_athlete_from_form(athlete_form)
                ensure_user_athlete_links(
                    request.user,
                    Athlete.objects.filter(pk=athlete.pk),
                    source="self_created",
                )
                available_ids.add(athlete.pk)
                selected_ids.add(athlete.pk)
            else:
                context = self._build_context(
                    request,
                    competition,
                    link,
                    athlete_form=athlete_form,
                    selected_ids=selected_ids,
                )
                return render(request, self.template_name, context)

        selected_ids = {athlete_id for athlete_id in selected_ids if athlete_id in available_ids}

        application, _ = CompetitionApplication.objects.get_or_create(
            competition=competition,
            user=request.user,
        )
        existing_ids = set(
            CompetitionEntry.objects.filter(
                competition=competition,
                application=application,
            ).values_list("athlete_id", flat=True)
        )
        to_remove = existing_ids - selected_ids
        if to_remove:
            CompetitionEntry.objects.filter(
                competition=competition,
                application=application,
                athlete_id__in=to_remove,
            ).delete()

        existing_for_competition = set(
            CompetitionEntry.objects.filter(
                competition=competition,
                athlete_id__in=selected_ids,
            ).values_list("athlete_id", flat=True)
        )
        to_add = selected_ids - existing_for_competition
        if to_add:
            CompetitionEntry.objects.bulk_create(
                [
                    CompetitionEntry(
                        competition=competition,
                        athlete_id=athlete_id,
                        application=application,
                    )
                    for athlete_id in to_add
                ]
            )

        messages.success(request, "Заявка сохранена.")
        context = self._build_context(
            request,
            competition,
            link,
            selected_ids=selected_ids,
        )
        return render(request, self.template_name, context)


class CompetitionApplyToggleView(LoginRequiredMixin, View):
    """AJAX add/remove athlete in public apply form without reload."""

    def post(self, request, pk, token):
        competition = get_object_or_404(Competition, pk=pk)
        link = get_object_or_404(
            CompetitionApplicationLink,
            competition=competition,
            token=token,
            is_active=True,
        )
        now = timezone.now()
        if link.expires_at and now > link.expires_at:
            return JsonResponse({"success": False, "error": "expired"}, status=403)

        action = request.POST.get("action")
        athlete_id = request.POST.get("athlete_id")
        if action not in {"add", "remove"} or not athlete_id:
            return JsonResponse({"success": False, "error": "bad_request"}, status=400)

        try:
            athlete_id_int = int(athlete_id)
        except (TypeError, ValueError):
            return JsonResponse({"success": False, "error": "bad_athlete"}, status=400)

        available_ids = set(
            get_available_athlete_queryset_for_user(request.user, ensure_family_links=True).values_list("id", flat=True)
        )
        if athlete_id_int not in available_ids:
            return JsonResponse({"success": False, "error": "forbidden"}, status=403)

        if action == "add":
            application, _ = CompetitionApplication.objects.get_or_create(
                competition=competition,
                user=request.user,
            )
            CompetitionEntry.objects.get_or_create(
                competition=competition,
                athlete_id=athlete_id_int,
                defaults={"application": application},
            )
            return JsonResponse({"success": True, "status": "added"})

        CompetitionEntry.objects.filter(
            competition=competition,
            athlete_id=athlete_id_int,
        ).delete()
        return JsonResponse({"success": True, "status": "removed"})


def _excel_date_from_serial(serial):
    from datetime import datetime, timedelta
    try:
        base = datetime(1899, 12, 30)
        return base + timedelta(days=int(serial))
    except Exception:
        return None


def competition_export(request, pk):
    competition = get_object_or_404(Competition, pk=pk)
    entries = competition.entries.select_related("athlete__person", "athlete__person__club").order_by(
        "athlete__person__surname", "athlete__person__name"
    )
    rows = []
    for idx, entry in enumerate(entries, start=1):
        person = entry.athlete.person
        dob = person.date_of_birth.strftime("%d.%m.%Y") if person.date_of_birth else ""
        year = person.date_of_birth.year if person.date_of_birth else ""
        rows.append(
            [
                str(idx),
                " ".join(filter(None, [person.surname, person.name, person.middlename])),
                dob,
                entry.athlete.rank or "",
                "Всеволожский",  # муниципальный район — по примеру файла
                person.club.name if person.club else "",
            ]
        )

    # XML Spreadsheet with styles
    from django.utils.encoding import force_str

    def cell(value, style=None):
        style_attr = f" ss:StyleID='{style}'" if style else ""
        return f"<Cell{style_attr}><Data ss:Type='String'>{force_str(value)}</Data></Cell>"

    header = f'ЗАЯВКА НА УЧАСТИЕ СПОРТСМЕНОВ В СОРЕВНОВАНИЯХ "{competition.name}" ПО ГОРНОЛЫЖНОМУ СПОРТУ'
    if competition.location:
        subloc = f'НА {str(competition.location).upper()}'
    else:
        subloc = ""
    date_part = ""
    if competition.start_date and competition.end_date and competition.start_date != competition.end_date:
        date_part = f"{fmt_ru_date(competition.start_date)} - {fmt_ru_date(competition.end_date)}"
    elif competition.start_date:
        date_part = fmt_ru_date(competition.start_date)
    elif competition.date:
        date_part = fmt_ru_date(competition.date)
    subtitle = " ".join(filter(None, [subloc, date_part, "от команды Всеволожского района"])).strip()

    table_rows = []
    # Title rows
    table_rows.append(f"<Row><Cell ss:MergeAcross='5' ss:StyleID='sTitle'><Data ss:Type='String'>{force_str(header)}</Data></Cell></Row>")
    table_rows.append(f"<Row><Cell ss:MergeAcross='5' ss:StyleID='sSubtitle'><Data ss:Type='String'>{force_str(subtitle)}</Data></Cell></Row>")
    # Header row
    headers = ["№ п/п", "Фамилия, Имя", "Дата рождения", "Спортивный разряд", "Муниципальный район", "ФСО/Отделение"]
    table_rows.append("<Row>" + "".join(cell(h, "sHead") for h in headers) + "</Row>")
    # Data rows
    for r in rows:
        table_rows.append("<Row>" + "".join(cell(val, "sCell") for val in r) + "</Row>")

    styles = """
    <Styles>
      <Style ss:ID="sTitle">
        <Font ss:Bold="1" ss:Size="12"/>
        <Alignment ss:Horizontal="Center"/>
      </Style>
      <Style ss:ID="sSubtitle">
        <Font ss:Bold="1" ss:Size="11"/>
        <Alignment ss:Horizontal="Center"/>
      </Style>
      <Style ss:ID="sHead">
        <Font ss:Bold="1"/>
        <Alignment ss:Horizontal="Center"/>
        <Borders>
          <Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1"/>
          <Border ss:Position="Top" ss:LineStyle="Continuous" ss:Weight="1"/>
          <Border ss:Position="Left" ss:LineStyle="Continuous" ss:Weight="1"/>
          <Border ss:Position="Right" ss:LineStyle="Continuous" ss:Weight="1"/>
        </Borders>
      </Style>
      <Style ss:ID="sCell">
        <Borders>
          <Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1"/>
          <Border ss:Position="Top" ss:LineStyle="Continuous" ss:Weight="1"/>
          <Border ss:Position="Left" ss:LineStyle="Continuous" ss:Weight="1"/>
          <Border ss:Position="Right" ss:LineStyle="Continuous" ss:Weight="1"/>
        </Borders>
      </Style>
    </Styles>
    """

    columns = (
        "<Column ss:AutoFitWidth='0' ss:Width='25'/>"
        "<Column ss:AutoFitWidth='0' ss:Width='220'/>"
        "<Column ss:AutoFitWidth='0' ss:Width='80'/>"
        "<Column ss:AutoFitWidth='0' ss:Width='80'/>"
        "<Column ss:AutoFitWidth='0' ss:Width='120'/>"
        "<Column ss:AutoFitWidth='0' ss:Width='120'/>"
    )

    xml_content = (
        "<?xml version='1.0'?>"
        "<?mso-application progid='Excel.Sheet'?>"
        "<Workbook xmlns='urn:schemas-microsoft-com:office:spreadsheet' "
        "xmlns:ss='urn:schemas-microsoft-com:office:spreadsheet'>"
        f"{styles}"
        "<Worksheet ss:Name='Общая заявка'><Table>"
        f"{columns}"
        + "".join(table_rows) +
        "</Table></Worksheet></Workbook>"
    )
    resp = HttpResponse(xml_content, content_type="application/vnd.ms-excel")
    resp["Content-Disposition"] = f'attachment; filename=\"zayavka_{competition.pk}.xls\"'
    return resp


MONTHS_RU = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


def fmt_ru_date(d):
    if not d:
        return ""
    return f"{d.day} {MONTHS_RU.get(d.month, '')} {d.year}"


def _group_entries_by_birth_year(entries):
    groups = [
        ("Мальчики и девочки 2016-2017 г.р.", (2016, 2017)),
        ("Мальчики и девочки 2014-2015 г.р.", (2014, 2015)),
        ("Юноши и девушки 2012-2013 г.р.", (2012, 2013)),
        ("Юноши и девушки 2010-2011 г.р.", (2010, 2011)),
    ]
    result = []
    for title, (start_year, end_year) in groups:
        filtered = [
            e for e in entries if e.athlete.person.date_of_birth and start_year <= e.athlete.person.date_of_birth.year <= end_year
        ]
        # keep deterministic order: by surname, name
        filtered.sort(key=lambda e: (e.athlete.person.surname or "", e.athlete.person.name or ""))
        result.append((title, filtered))
    return result


def competition_export_type2(request, pk):
    competition = get_object_or_404(Competition, pk=pk)
    entries = list(
        competition.entries.select_related("athlete__person", "athlete__person__club").all()
    )
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Side, Font
    except ImportError:
        return HttpResponse("openpyxl не установлен", status=500)

    wb = Workbook()
    ws = wb.active
    ws.title = "Именная заявка"

    thin = Side(border_style="thin", color="000000")
    border_all = Border(top=thin, bottom=thin, left=thin, right=thin)

    def write_header():
        ws.merge_cells("A1:G1")
        ws.merge_cells("A2:G2")
        ws.merge_cells("A3:G3")
        ws.merge_cells("A4:G4")
        ws.merge_cells("A5:G5")
        ws.merge_cells("A6:G6")
        ws.merge_cells("A7:G7")
        ws.merge_cells("A8:G8")

        ws["A2"].value = "ИМЕННАЯ ЗАЯВКА"
        ws["A2"].font = Font(bold=True, size=14)
        ws["A2"].alignment = Alignment(horizontal="center")

        ws["A3"].value = f"на участие в соревнованиях «{competition.name}»"
        ws["A3"].alignment = Alignment(horizontal="center")

        ws["A4"].value = f"Место проведения: {competition.location}"
        date_str = ""
        if competition.start_date and competition.end_date and competition.start_date != competition.end_date:
            date_str = f"{fmt_ru_date(competition.start_date)} — {fmt_ru_date(competition.end_date)}"
        elif competition.start_date:
            date_str = fmt_ru_date(competition.start_date)
        elif competition.date:
            date_str = fmt_ru_date(competition.date)
        ws["A5"].value = f"Сроки проведения: {date_str}"
        ws["A7"].value = "От физкультурно-спортивной организации: ________________________________________________"
        ws["A8"].value = "Руководитель команды: __________________________ Контакты: _______________________"
        ws["A9"].value = "Сопровождающий команды: ________________________ Контакты: _______________________"

    write_header()

    start_row = 11
    headers = ["№ п/п", "Фамилия, Имя", "Д.р.", "Спортивный разряд", "Наименование ФСО", "Муниципальный район", "Допуск врача (подпись, штамп)"]
    column_widths = [5, 26, 10, 18, 26, 18, 24]
    for idx, width in enumerate(column_widths, start=1):
        ws.column_dimensions[chr(64 + idx)].width = width

    grouped = _group_entries_by_birth_year(entries)
    row_cursor = start_row
    for group_title, group_entries in grouped:
        ws.merge_cells(start_row=row_cursor, start_column=1, end_row=row_cursor, end_column=7)
        cell = ws.cell(row=row_cursor, column=1, value=group_title)
        cell.font = Font(bold=True, underline="single")
        row_cursor += 1

        # header
        for col_idx, title in enumerate(headers, start=1):
            c = ws.cell(row=row_cursor, column=col_idx, value=title)
            c.font = Font(bold=True)
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            c.border = border_all
        row_cursor += 1

        if group_entries:
            for idx, entry in enumerate(group_entries, start=1):
                person = entry.athlete.person
                values = [
                    idx,
                    " ".join(filter(None, [person.surname, person.name])),
                    person.date_of_birth.strftime("%d.%m.%Y") if person.date_of_birth else "",
                    entry.athlete.rank or "",
                    person.club.name if person.club else "",
                    "Всеволожский",
                    "",
                ]
                for col_idx, val in enumerate(values, start=1):
                    c = ws.cell(row=row_cursor, column=col_idx, value=val)
                    c.border = border_all
                    c.alignment = Alignment(vertical="center")
                row_cursor += 1
        else:
            # two empty rows with numbering
            for idx in (1, 2):
                ws.cell(row=row_cursor, column=1, value=idx).border = border_all
                for col_idx in range(2, 8):
                    ws.cell(row=row_cursor, column=col_idx).border = border_all
                row_cursor += 1

        # spacer line
        row_cursor += 1

    # footer
    ws.merge_cells(start_row=row_cursor, start_column=1, end_row=row_cursor, end_column=7)
    ws.cell(row=row_cursor, column=1, value="К соревнованиям допущено ______ человек.")
    row_cursor += 2
    ws.merge_cells(start_row=row_cursor, start_column=1, end_row=row_cursor, end_column=7)
    ws.cell(row=row_cursor, column=1, value="Врач (ФИО) __________________________ / ____________ / ____________/")
    row_cursor += 1
    ws.merge_cells(start_row=row_cursor, start_column=1, end_row=row_cursor, end_column=7)
    ws.cell(row=row_cursor, column=1, value="Представитель команды __________________________ / ____________ / ____________/")

    # align all text left by default
    for row in ws.iter_rows(min_row=1, max_row=row_cursor, min_col=1, max_col=7):
        for cell in row:
            if cell.alignment is None:
                cell.alignment = Alignment(horizontal="left", vertical="center")

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    resp["Content-Disposition"] = f'attachment; filename=\"zayavka_{competition.pk}_type2.xlsx\"'
    return resp


def _get_org_from_entries(entries):
    for entry in entries:
        if entry.athlete.person.club:
            return entry.athlete.person.club.name
    return ""


def _medical_value(entry):
    cert = entry.athlete.medical_certificate
    if cert:
        return cert
    return "Да"  # default marker like on примеры


def competition_export_word_type1(request, pk):
    competition = get_object_or_404(Competition, pk=pk)
    entries = list(
        competition.entries.select_related("athlete__person", "athlete__person__club").all()
    )
    try:
        from docx import Document
        from docx.shared import Cm, Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
    except ImportError:
        return HttpResponse("python-docx не установлен", status=500)

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(2)
    section.right_margin = Cm(2)

    def add_para(text, bold=False, align="left", underline=False, font_size=12):
        p = doc.add_paragraph()
        if align == "center":
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(text)
        run.bold = bold
        if underline:
            run.underline = True
        run.font.size = Pt(font_size)
        run.font.name = "Times New Roman"
        r = run._element
        r.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
        return p

    add_para("ИМЕННАЯ ЗАЯВКА", bold=True, align="center", font_size=14)
    add_para(f"на участие в соревнованиях «{competition.name}»", align="center")
    add_para(f"Место проведения: {competition.location}")
    date_str = ""
    if competition.start_date and competition.end_date and competition.start_date != competition.end_date:
        date_str = f"{fmt_ru_date(competition.start_date)} — {fmt_ru_date(competition.end_date)}"
    elif competition.start_date:
        date_str = fmt_ru_date(competition.start_date)
    elif competition.date:
        date_str = fmt_ru_date(competition.date)
    add_para(f"Сроки проведения: {date_str}")
    add_para("От физкультурно-спортивной организации: ________________________________________________")
    add_para("Руководитель команды: __________________________ Контакты: _______________________")
    add_para("Сопровождающий команды: ________________________ Контакты: _______________________")

    table_headers = ["№ п/п", "Фамилия, Имя", "Д.р.", "Спортивный разряд", "Наименование ФСО", "Муниципальный район", "Допуск врача (подпись, штамп)"]
    # ширины под макет, суммарно ≈ ширина страницы после полей
    # чуть сузил, чтобы гарантированно вписаться в страницу с полями 2 см
    col_width_cm = [0.9, 4.4, 1.5, 1.9, 2.7, 2.1, 2.0]

    grouped = _group_entries_by_birth_year(entries)
    for title, group_entries in grouped:
        add_para("")  # spacer
        add_para(title, bold=True, underline=True)
        table = doc.add_table(rows=1, cols=len(table_headers))
        table.style = "Table Grid"
        table.autofit = False
        table.allow_autofit = False
        for i, width in enumerate(col_width_cm):
            table.columns[i].width = Cm(width)
        hdr_cells = table.rows[0].cells
        for idx, text in enumerate(table_headers):
            hdr_cells[idx].text = text
            hdr_cells[idx].paragraphs[0].runs[0].font.bold = True
            hdr_cells[idx].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

        rows_to_render = group_entries if group_entries else [None, None]
        for idx, entry in enumerate(rows_to_render, start=1):
            row_cells = table.add_row().cells
            if entry is None:
                vals = [idx, "", "", "", "", "", ""]
            else:
                person = entry.athlete.person
                vals = [
                    idx,
                    " ".join(filter(None, [person.surname, person.name])),
                    person.date_of_birth.strftime("%d.%m.%Y") if person.date_of_birth else "",
                    entry.athlete.rank or "",
                    person.club.name if person.club else "",
                    "Всеволожский",
                    "",
                ]
            for ci, val in enumerate(vals):
                row_cells[ci].text = str(val) if val is not None else ""

    add_para("")
    add_para("К соревнованиям допущено ______ человек.")
    add_para("Врач (ФИО) __________________________ / ____________ / ____________/")
    add_para("Представитель команды __________________________ / ____________ / ____________/")

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    resp["Content-Disposition"] = f'attachment; filename=\"zayavka_{competition.pk}_w1.docx\"'
    return resp


def _set_font(run, size=12, bold=False, underline=False, color=None):
    from docx.shared import Pt

    run.font.size = Pt(size)
    run.font.name = "Times New Roman"
    run.bold = bold
    run.underline = underline
    if color:
        run.font.color.rgb = color


def competition_export_word_type2(request, pk):
    competition = get_object_or_404(Competition, pk=pk)
    entries = list(
        competition.entries.select_related("athlete__person", "athlete__person__club").order_by(
            "athlete__person__surname", "athlete__person__name"
        )
    )
    try:
        from docx import Document
        from docx.shared import Cm, Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
    except ImportError:
        return HttpResponse("python-docx не установлен", status=500)

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.5)

    def add_p(text, bold=False, center=False, size=12, underline=False, color=None):
        p = doc.add_paragraph()
        if center:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(text)
        _set_font(run, size=size, bold=bold, underline=underline, color=color)
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
        return p

    org = _get_org_from_entries(entries) or "________________________________"

    add_p("ИМЕННАЯ ЗАЯВКА", bold=True, center=True, size=14)
    add_p(f"на участие в физкультурных соревнованиях по горнолыжному спорту «{competition.name}»", center=True)
    add_p(f"Место проведения: {competition.location}")
    date_str = fmt_ru_date(competition.start_date or competition.date)
    add_p(f"Дата проведения: {date_str}")
    add_p(f"от организации: {org}", color=RGBColor(0xCC, 0x00, 0x00))

    headers = ["№ п/п", "ФИО (полностью)", "Год рожд. (полностью)", "Спорт. разряд", "Допуск Врача"]
    widths = [0.9, 6.8, 2.4, 2.1, 2.0]  # подгонка под ширину страницы с полями 2.5 см

    groups = [
        ("Девочки 2016-2017 г.р.", (2016, 2017), "female"),
        ("Мальчики 2016-2017 г.р.", (2016, 2017), "male"),
        ("Мальчики 2014-2015 г.р.", (2014, 2015), "male"),
        ("Девушки 2012-2013 г.р.", (2012, 2013), "female"),
        ("Юноши 2010-2011 г.р.", (2010, 2011), "male"),
    ]

    def in_bucket(entry, start, end, gender):
        dob = entry.athlete.person.date_of_birth
        if not dob:
            return False
        if gender and entry.athlete.person.gender != gender:
            return False
        return start <= dob.year <= end

    for title, (start, end), gender in groups:
        subset = [e for e in entries if in_bucket(e, start, end, gender)]
        if not subset:
            continue
        add_p("")  # spacer
        add_p(title, bold=True)
        table = doc.add_table(rows=1, cols=len(headers))
        table.style = "Table Grid"
        table.autofit = False
        table.allow_autofit = False
        for idx, w in enumerate(widths):
            table.columns[idx].width = Cm(w)
        for i, h in enumerate(headers):
            cell = table.rows[0].cells[i]
            cell.text = h
            cell.paragraphs[0].runs[0].bold = True
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        for idx, entry in enumerate(subset, start=1):
            p = entry.athlete.person
            vals = [
                idx,
                " ".join(filter(None, [p.surname, p.name, p.middlename])),
                p.date_of_birth.strftime("%d.%m.%Y") if p.date_of_birth else "",
                entry.athlete.rank or "",
                _medical_value(entry),
            ]
            row = table.add_row().cells
            for ci, val in enumerate(vals):
                row[ci].text = str(val)

    add_p("")
    add_p(f"К соревнованиям допущено {len(entries)} человек")
    add_p("Врач (ФИО) _____________________________   м.п. Дата: ____________")
    add_p("Руководитель команды: ________________________")

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    resp["Content-Disposition"] = f'attachment; filename=\"zayavka_{competition.pk}_w2.docx\"'
    return resp


def competition_export_word_type3(request, pk):
    competition = get_object_or_404(Competition, pk=pk)
    entries = list(
        competition.entries.select_related("athlete__person", "athlete__person__club").order_by(
            "athlete__person__surname", "athlete__person__name"
        )
    )
    try:
        from docx import Document
        from docx.shared import Cm, Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
    except ImportError:
        return HttpResponse("python-docx не установлен", status=500)

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)

    def add_p(text, bold=False, center=False, size=12, underline=False, color=None):
        p = doc.add_paragraph()
        if center:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(text)
        _set_font(run, size=size, bold=bold, underline=underline, color=color)
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
        return p

    org = _get_org_from_entries(entries) or "Kanaev Ski Club"
    contact = "+7(921)559-51-14"

    add_p("ИМЕННАЯ ЗАЯВКА", bold=True, center=True, size=14)
    add_p(f"на участие в соревнованиях «{competition.name}»", center=True)
    add_p(f"место проведение {competition.location}")
    add_p(f"сроки проведения {fmt_ru_date(competition.start_date or competition.date)}")
    add_p(f"от физкультурно-спортивной организации {org}")
    add_p("руководитель команды Канаев Д.Т.")
    add_p(f"сопровождающий команды Козлеев М.С.")
    add_p(f"контакты {contact}")

    headers = ["№", "Фамилия, Имя", "Год рождения", "Спортивный разряд", "Наименование ФСО"]
    widths = [0.9, 6.0, 2.5, 2.5, 3.4]  # адаптировано под ширину страницы

    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.autofit = False
    table.allow_autofit = False
    for idx, w in enumerate(widths):
        table.columns[idx].width = Cm(w)
    for i, h in enumerate(headers):
        cell = table.rows[0].cells[i]
        cell.text = h
        cell.paragraphs[0].runs[0].bold = True
        cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER

    for idx, entry in enumerate(entries, start=1):
        p = entry.athlete.person
        vals = [
            idx,
            " ".join(filter(None, [p.surname, p.name])),
            p.date_of_birth.year if p.date_of_birth else "",
            entry.athlete.rank or "",
            p.club.name if p.club else org,
        ]
        row = table.add_row().cells
        for ci, val in enumerate(vals):
            row[ci].text = str(val)

    add_p("")
    add_p(f"К соревнованиям допущено {len(entries)} человек")
    add_p("Врач (ФИО) ________________________________")
    add_p("")
    add_p("Руководитель физкультурно-спортивной организации")
    add_p("______________________________________________", center=True)
    add_p("Подпись печать", center=True)

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    resp["Content-Disposition"] = f'attachment; filename=\"zayavka_{competition.pk}_w3.docx\"'
    return resp


def competition_export_word_type4(request, pk):
    competition = get_object_or_404(Competition, pk=pk)
    entries = list(
        competition.entries.select_related("athlete__person", "athlete__person__club").order_by(
            "athlete__person__surname", "athlete__person__name"
        )
    )
    try:
        from docx import Document
        from docx.shared import Cm, Pt
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
    except ImportError:
        return HttpResponse("python-docx не установлен", status=500)

    doc = Document()
    section = doc.sections[0]
    section.top_margin = Cm(2)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(1.8)
    section.right_margin = Cm(1.8)

    def add_p(text, bold=False, center=False, size=12):
        p = doc.add_paragraph()
        if center:
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = p.add_run(text)
        _set_font(run, size=size, bold=bold)
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
        return p

    org = _get_org_from_entries(entries) or "Kanaev Ski Club"

    add_p("ИМЕННАЯ ЗАЯВКА", bold=True, center=True, size=14)
    add_p(f"на участие в соревнованиях {competition.name}")
    add_p(f"место проведение: {competition.location}")
    add_p(f"сроки проведения {fmt_ru_date(competition.start_date or competition.date)}")
    add_p(f"от физкультурно-спортивной организации: {org}")
    add_p("руководитель команды Канаев Д.Т.")
    add_p("контакты +79811221000")
    add_p("сопровождающий команды: Козлеев М.С.")
    add_p("контакты +79215595114")

    headers = ["№ п/п", "Фамилия, Имя", "Год рождения", "Спортивный разряд", "Наименование ФСО", "Муниципальный район", "Допуск врача"]
    widths = [0.8, 4.5, 1.7, 2.0, 2.6, 2.1, 1.8]  # подгонка под ширину страницы

    groups = [
        ("Девочки 2016-2017", (2016, 2017), "female"),
        ("Мальчики 2016-2017 г.р.", (2016, 2017), "male"),
        ("Мальчики 2014-2015 г.р.", (2014, 2015), "male"),
    ]

    def in_bucket(entry, start, end, gender):
        dob = entry.athlete.person.date_of_birth
        if not dob:
            return False
        if gender and entry.athlete.person.gender != gender:
            return False
        return start <= dob.year <= end

    for title, (start, end), gender in groups:
        subset = [e for e in entries if in_bucket(e, start, end, gender)]
        if not subset:
            continue
        add_p("")
        add_p(title, bold=True)
        table = doc.add_table(rows=1, cols=len(headers))
        table.style = "Table Grid"
        table.autofit = False
        table.allow_autofit = False
        for idx, w in enumerate(widths):
            table.columns[idx].width = Cm(w)
        for i, h in enumerate(headers):
            cell = table.rows[0].cells[i]
            cell.text = h
            cell.paragraphs[0].runs[0].bold = True
            cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        for idx, entry in enumerate(subset, start=1):
            p = entry.athlete.person
            vals = [
                idx,
                " ".join(filter(None, [p.surname, p.name])),
                p.date_of_birth.strftime("%d.%m.%Y") if p.date_of_birth else "",
                entry.athlete.rank or "",
                p.club.name if p.club else org,
                "Всеволожский",
                _medical_value(entry),
            ]
            row = table.add_row().cells
            for ci, val in enumerate(vals):
                row[ci].text = str(val)

    add_p("")
    add_p(f"К соревнованиям допущено {len(entries)} человек")
    add_p("Врач (ФИО) __________________________")
    add_p("Представитель команды __________________________")
    add_p("Руководитель физкультурно-спортивной организации")
    add_p("______________________________________________", center=True)
    add_p("Подпись печать", center=True)

    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    resp = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    resp["Content-Disposition"] = f'attachment; filename=\"zayavka_{competition.pk}_w4.docx\"'
    return resp
