from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView

from school.forms import PersonForm
from school.models import Person

# CRUD для модели "Person"
class PersonListView(LoginRequiredMixin, ListView):
    """Контроллер для работы с БД членов клуба - список"""

    model = Person
    template_name = "members/person_list.html"


class PersonDetailView(LoginRequiredMixin, DetailView):
    """Контроллер для работы с БД членов клуба - инфо о персоне"""

    model = Person
    template_name = "members/person_detail.html"


class PersonCreateView(LoginRequiredMixin, CreateView):
    """Контроллер для работы с БД членов клуба - создание"""

    model = Person
    template_name = "members/person_form.html"
    form_class = PersonForm


class PersonUpdateView(LoginRequiredMixin, UpdateView):
    """Контроллер для работы с БД членов клуба - изменение"""

    model = Person
    template_name = "members/person_form.html"
    form_class = PersonForm


class PersonDeleteView(LoginRequiredMixin, DeleteView):
    """Контроллер для работы с БД членов клуба - удаление"""

    model = Person
    success_url = reverse_lazy("members:person_list")
    template_name = "members/person_confirm_delete.html"
