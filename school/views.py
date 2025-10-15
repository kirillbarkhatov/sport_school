from django.contrib.auth.decorators import login_required
from django.shortcuts import render, get_object_or_404, redirect
from django.views.generic import TemplateView, ListView

from school.forms import AthleteForm, PersonForm, FamilyForm, FamilyMemberForm
from school.models import Athlete, Family, FamilyMember
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
            upcoming_classes=classes_qs.order_by("date")[:5],
        )
        return context


class AthleteListView(ApprovedUserRequiredMixin, ListView):
    model = Athlete

    def get_queryset(self):
        return get_athlete_queryset_for_user(self.request.user).prefetch_related('groups_athletes')


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

    return render(request, 'athletes/edit_form.html', {
        'athlete_form': athlete_form,
        'person_form': person_form,
        'family_form': family_form,
        'family_member_forms': family_member_forms,
        'athlete': athlete,
    })
