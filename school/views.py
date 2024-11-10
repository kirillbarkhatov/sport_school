from django.shortcuts import render, get_object_or_404
from django.views.generic import TemplateView, ListView

from school.forms import AthleteForm, PersonForm
from school.models import Athlete


# Create your views here.


class IndexView(TemplateView):
    """Стартовая страница"""
    template_name = "school/index.html"


class AthleteListView(ListView):
    model = Athlete

    def get_queryset(self):
        # Предзагрузка групп, связанных с каждым спортсменом, чтобы минимизировать количество запросов

        return Athlete.objects.prefetch_related('groups_athletes')


def edit_athlete(request, athlete_id):
    athlete = get_object_or_404(Athlete, id=athlete_id)
    person = athlete.person  # Получаем связанные данные из модели Person

    if request.method == "POST":
        athlete_form = AthleteForm(request.POST, instance=athlete)
        person_form = PersonForm(request.POST, instance=person)
        if athlete_form.is_valid() and person_form.is_valid():
            athlete_form.save()
            person_form.save()
    else:
        athlete_form = AthleteForm(instance=athlete)
        person_form = PersonForm(instance=person)

    return render(request, 'athletes/edit_form.html', {
        'athlete_form': athlete_form,
        'person_form': person_form,
        'athlete': athlete,
    })
