from django.urls import reverse_lazy, reverse
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView

from users.mixins import ApprovedUserRequiredMixin
from users.utils import get_person_queryset_for_user
from school.forms import PersonForm, FamilyForm
from school.models import Person, Family

# CRUD для модели "Person"
class PersonListView(ApprovedUserRequiredMixin, ListView):
    """Контроллер для работы с БД членов клуба - список"""

    model = Person
    template_name = "members/person_list.html"

    def get_queryset(self):
        return get_person_queryset_for_user(self.request.user).prefetch_related("familymember_set__family")


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

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


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
    success_url = reverse_lazy("members:person_list")
    template_name = "members/person_confirm_delete.html"

    def get_queryset(self):
        return get_person_queryset_for_user(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


class FamilyListView(ApprovedUserRequiredMixin, ListView):
    model = Family
    template_name = "members/family_list.html"

    def get_queryset(self):
        base_qs = Family.objects.prefetch_related("members__person", "users")
        if self.request.user.is_staff or self.request.user.is_superuser:
            return base_qs
        family_ids = self.request.user.get_accessible_family_ids()
        return base_qs.filter(id__in=family_ids)


class FamilyDetailView(ApprovedUserRequiredMixin, DetailView):
    model = Family
    template_name = "members/family_detail.html"

    def get_queryset(self):
        base = Family.objects.prefetch_related("members__person", "users")
        if self.request.user.is_staff or self.request.user.is_superuser:
            return base
        return base.filter(id__in=self.request.user.get_accessible_family_ids())


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
