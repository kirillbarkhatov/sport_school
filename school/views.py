from django.shortcuts import render
from django.views.generic import TemplateView, ListView
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