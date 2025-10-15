from django.contrib.auth.mixins import LoginRequiredMixin
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


class GroupListView(LoginRequiredMixin, ListView):
    model = Group
    template_name = "groups/group_list.html"


class GroupDetailView(LoginRequiredMixin, DetailView):
    model = Group
    template_name = "groups/group_detail.html"


class GroupCreateView(LoginRequiredMixin, CreateView):
    model = Group
    form_class = GroupForm
    template_name = "groups/group_form.html"
    success_url = reverse_lazy("groups:group_list")


class GroupUpdateView(LoginRequiredMixin, UpdateView):
    model = Group
    form_class = GroupForm
    template_name = "groups/group_form.html"
    success_url = reverse_lazy("groups:group_list")


class GroupDeleteView(LoginRequiredMixin, DeleteView):
    model = Group
    template_name = "groups/group_confirm_delete.html"
    success_url = reverse_lazy("groups:group_list")
