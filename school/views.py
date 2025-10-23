import json
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView, ListView

from school.forms import AthleteForm, PersonForm, FamilyForm, FamilyMemberForm
from school.models import Athlete, Family, FamilyMember, Group
from users.mixins import ApprovedUserRequiredMixin
from users.utils import (
    get_athlete_queryset_for_user,
    get_person_queryset_for_user,
    get_group_queryset_for_user,
    get_class_queryset_for_user,
)


# Create your views here.


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
