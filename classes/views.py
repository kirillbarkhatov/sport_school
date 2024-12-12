from django.forms import formset_factory
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView

from school.forms import ClassForm, AthleteSelectionForm
from school.models import Class, Athlete, ClassEnrollment


# CRUD для модели "Class"
class ClassListView(ListView):
    """Контроллер для работы с БД членов клуба - список"""

    model = Class
    template_name = "classes/class_list.html"


class ClassDetailView(DetailView):
    """Контроллер для работы с БД членов клуба - инфо о персоне"""

    model = Class
    template_name = "classes/class_detail.html"


class ClassCreateView(CreateView):
    """Контроллер для работы с БД членов клуба - создание"""

    model = Class
    template_name = "classes/class_form.html"
    form_class = ClassForm
    success_url = reverse_lazy("classes:class_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if self.request.POST:
            # Передаем данные POST в форму выбора спортсменов
            context['athlete_form'] = AthleteSelectionForm(self.request.POST)
        else:
            # При GET запросе предзаполняем форму спортсменами
            context['athlete_form'] = AthleteSelectionForm(
                initial={
                    'athletes': Athlete.objects.filter(
                        class_enrollments__class_instance=self.object  # Связанные спортсмены
                    )
                }
            )

        return context

    def form_valid(self, form):
        context = self.get_context_data()
        athlete_form = context['athlete_form']

        if form.is_valid() and athlete_form.is_valid():
            # Сохраняем занятие
            self.object = form.save()

            # Обновляем записи ClassEnrollment
            ClassEnrollment.objects.filter(class_instance=self.object).delete()
            for athlete in athlete_form.cleaned_data['athletes']:
                ClassEnrollment.objects.create(
                    class_instance=self.object,
                    athlete=athlete
                )

            return HttpResponseRedirect(self.get_success_url())

        return self.form_invalid(form)


class ClassUpdateView(UpdateView):
    """Контроллер для работы с БД членов клуба - изменение"""

    model = Class
    template_name = "classes/class_form.html"
    form_class = ClassForm
    success_url = reverse_lazy("classes:class_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if self.request.POST:
            # Передаем данные POST в форму выбора спортсменов
            context['athlete_form'] = AthleteSelectionForm(self.request.POST)
        else:
            # При GET запросе предзаполняем форму спортсменами
            context['athlete_form'] = AthleteSelectionForm(
                initial={
                    'athletes': Athlete.objects.filter(
                        class_enrollments__class_instance=self.object  # Связанные спортсмены
                    )
                }
            )

        return context

    def form_valid(self, form):
        context = self.get_context_data()
        athlete_form = context['athlete_form']

        if form.is_valid() and athlete_form.is_valid():
            # Сохраняем изменения занятия
            self.object = form.save()

            # Обновляем записи ClassEnrollment
            ClassEnrollment.objects.filter(class_instance=self.object).delete()
            for athlete in athlete_form.cleaned_data['athletes']:
                ClassEnrollment.objects.create(
                    class_instance=self.object,
                    athlete=athlete
                )

            return HttpResponseRedirect(self.get_success_url())

        return self.form_invalid(form)


class ClassDeleteView(DeleteView):
    """Контроллер для работы с БД членов клуба - удаление"""

    model = Class
    success_url = reverse_lazy("classes:class_list")
    template_name = "classes/class_confirm_delete.html"
