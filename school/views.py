from django.shortcuts import render, get_object_or_404
from django.views.generic import TemplateView, ListView

from school.forms import AthleteForm, PersonForm, FamilyForm, FamilyMemberForm
from school.models import Athlete, Family, FamilyMember


# Create your views here.


class IndexView(TemplateView):
    """Стартовая страница"""
    template_name = "school/index.html"


class AthleteListView(ListView):
    model = Athlete

    def get_queryset(self):
        # Предзагрузка групп, связанных с каждым спортсменом, чтобы минимизировать количество запросов

        return Athlete.objects.prefetch_related('groups_athletes')


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


def edit_athlete(request, athlete_id):
    athlete = get_object_or_404(Athlete, id=athlete_id)
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
