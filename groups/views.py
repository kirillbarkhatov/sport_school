from django.urls import reverse_lazy
from django.views.generic import (
    ListView,
    DetailView,
    CreateView,
    UpdateView,
    DeleteView,
)

from school.forms import GroupForm
from school.models import Group
from users.mixins import ApprovedUserRequiredMixin
from users.utils import get_group_queryset_for_user, get_athlete_queryset_for_user


class GroupListView(ApprovedUserRequiredMixin, ListView):
    model = Group
    template_name = "groups/group_list.html"

    def get_queryset(self):
        return get_group_queryset_for_user(self.request.user)


class GroupDetailView(ApprovedUserRequiredMixin, DetailView):
    model = Group
    template_name = "groups/group_detail.html"

    def get_queryset(self):
        return get_group_queryset_for_user(self.request.user)


class GroupCreateView(ApprovedUserRequiredMixin, CreateView):
    model = Group
    form_class = GroupForm
    template_name = "groups/group_form.html"
    success_url = reverse_lazy("groups:group_list")

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.request.user.is_staff and not self.request.user.is_superuser:
            form.fields["athletes"].queryset = get_athlete_queryset_for_user(self.request.user)
        return form


class GroupUpdateView(ApprovedUserRequiredMixin, UpdateView):
    model = Group
    form_class = GroupForm
    template_name = "groups/group_form.html"
    success_url = reverse_lazy("groups:group_list")

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if not self.request.user.is_staff and not self.request.user.is_superuser:
            form.fields["athletes"].queryset = get_athlete_queryset_for_user(self.request.user)
        return form


class GroupDeleteView(ApprovedUserRequiredMixin, DeleteView):
    model = Group
    template_name = "groups/group_confirm_delete.html"
    success_url = reverse_lazy("groups:group_list")

    def get_queryset(self):
        return get_group_queryset_for_user(self.request.user)
