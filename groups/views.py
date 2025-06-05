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


class GroupListView(ListView):
    model = Group
    template_name = "groups/group_list.html"


class GroupDetailView(DetailView):
    model = Group
    template_name = "groups/group_detail.html"


class GroupCreateView(CreateView):
    model = Group
    form_class = GroupForm
    template_name = "groups/group_form.html"
    success_url = reverse_lazy("groups:group_list")


class GroupUpdateView(UpdateView):
    model = Group
    form_class = GroupForm
    template_name = "groups/group_form.html"
    success_url = reverse_lazy("groups:group_list")


class GroupDeleteView(DeleteView):
    model = Group
    template_name = "groups/group_confirm_delete.html"
    success_url = reverse_lazy("groups:group_list")
