from django.contrib import messages
from django.db import models
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView, FormView

from classes.forms import ClassNotificationForm
from notifications.models import Notification
from notifications.services import deliver_notification
from school.forms import ClassForm, AthleteSelectionForm
from school.models import Class, Athlete, ClassEnrollment, FamilyMember
from users.mixins import ApprovedUserRequiredMixin
from users.utils import (
    get_class_queryset_for_user,
    get_group_queryset_for_user,
    get_athlete_queryset_for_user,
)
from users.models import User


# CRUD для модели "Class"
class ClassListView(ApprovedUserRequiredMixin, ListView):
    """Контроллер для работы с БД членов клуба - список"""

    model = Class
    template_name = "classes/class_list.html"

    def get_queryset(self):
        return get_class_queryset_for_user(self.request.user).select_related("group")


class ClassDetailView(ApprovedUserRequiredMixin, DetailView):
    """Контроллер для работы с БД членов клуба - инфо о персоне"""

    model = Class
    template_name = "classes/class_detail.html"

    def get_queryset(self):
        return get_class_queryset_for_user(self.request.user).select_related("group").prefetch_related("enrollments__athlete__person")


class ClassCreateView(ApprovedUserRequiredMixin, CreateView):
    """Контроллер для работы с БД членов клуба - создание"""

    model = Class
    template_name = "classes/class_form.html"
    form_class = ClassForm
    success_url = reverse_lazy("classes:class_list")

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.request.user.is_staff and not self.request.user.is_superuser:
            form.fields["group"].queryset = get_group_queryset_for_user(self.request.user)
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        athlete_qs = get_athlete_queryset_for_user(self.request.user)

        if self.request.POST:
            context['athlete_form'] = AthleteSelectionForm(self.request.POST, user=self.request.user)
        else:
            context['athlete_form'] = AthleteSelectionForm(
                user=self.request.user,
                initial={
                    'athletes': athlete_qs.filter(
                        class_enrollments__class_instance=self.object
                    ) if self.object else athlete_qs.none()
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


class ClassUpdateView(ApprovedUserRequiredMixin, UpdateView):
    """Контроллер для работы с БД членов клуба - изменение"""

    model = Class
    template_name = "classes/class_form.html"
    form_class = ClassForm
    success_url = reverse_lazy("classes:class_list")

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.request.user.is_staff and not self.request.user.is_superuser:
            form.fields["group"].queryset = get_group_queryset_for_user(self.request.user)
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        athlete_qs = get_athlete_queryset_for_user(self.request.user)

        if self.request.POST:
            context['athlete_form'] = AthleteSelectionForm(self.request.POST, user=self.request.user)
        else:
            context['athlete_form'] = AthleteSelectionForm(
                user=self.request.user,
                initial={
                    'athletes': athlete_qs.filter(
                        class_enrollments__class_instance=self.object
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


class ClassDeleteView(ApprovedUserRequiredMixin, DeleteView):
    """Контроллер для работы с БД членов клуба - удаление"""

    model = Class
    success_url = reverse_lazy("classes:class_list")
    template_name = "classes/class_confirm_delete.html"

    def get_queryset(self):
        return get_class_queryset_for_user(self.request.user)


class ClassNotificationView(ApprovedUserRequiredMixin, FormView):
    template_name = "classes/class_notification_form.html"
    form_class = ClassNotificationForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def get_class_instance(self):
        queryset = get_class_queryset_for_user(self.request.user)
        return get_object_or_404(queryset.select_related("group"), pk=self.kwargs["pk"])

    def get_recipients_queryset(self):
        class_instance = self.get_class_instance()
        athlete_ids = class_instance.enrollments.values_list("athlete_id", flat=True)
        person_ids = list(
            Athlete.objects.filter(id__in=athlete_ids).values_list("person_id", flat=True)
        )
        family_ids = list(
            FamilyMember.objects.filter(person_id__in=person_ids).values_list("family_id", flat=True)
        )

        return User.objects.filter(
            models.Q(family_id__in=family_ids)
            | models.Q(person_id__in=person_ids)
        ).filter(is_approved=True).distinct()

    def get_initial(self):
        class_instance = self.get_class_instance()
        group_name = class_instance.group.name
        start_time = class_instance.date.strftime("%d.%m %H:%M")
        equipment_hint = ""
        if class_instance.equipment:
            equipment_hint = (
                "\nЭкипировка: " + class_instance.get_equipment_display()
            )
        training_type_display = class_instance.get_training_type_display()
        location_display = class_instance.get_location_display()
        return {
            "title": f"{training_type_display} — {group_name} {start_time}",
            "message": (
                f"Здравствуйте! Напоминаем о тренировке {training_type_display.lower()} группы {group_name} "
                f"{start_time} в {location_display}.{equipment_hint}\n"
                "Пожалуйста, подтвердите участие спортсмена."
            ),
            "recipients": list(self.get_recipients_queryset().values_list("pk", flat=True)),
        }

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["recipients_queryset"] = self.get_recipients_queryset()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["class_instance"] = self.get_class_instance()
        context["has_recipients"] = self.get_recipients_queryset().exists()
        return context

    def form_valid(self, form):
        class_instance = self.get_class_instance()
        recipients = form.cleaned_data["recipients"]

        notification = Notification.objects.create(
            title=form.cleaned_data["title"],
            message=form.cleaned_data["message"],
            send_to_telegram=form.cleaned_data["send_to_telegram"],
            send_to_email=form.cleaned_data["send_to_email"],
        )
        notification.users.set(recipients)
        deliver_notification(notification, users=recipients)

        messages.success(
            self.request,
            "Рассылка отправлена выбранным семьям.",
        )
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse_lazy("classes:class_detail", kwargs={"pk": self.kwargs["pk"]})
