import json

from django.http import JsonResponse
from django.urls import reverse_lazy
from django.views import View
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
        return (
            get_group_queryset_for_user(self.request.user)
            .prefetch_related("athletes__person", "coaches__person")
            .order_by("name")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["available_athletes"] = (
            get_athlete_queryset_for_user(user)
            .select_related("person")
            .order_by("person__surname", "person__name")
        )
        return context


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


class GroupMembershipUpdateView(ApprovedUserRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        group = (
            get_group_queryset_for_user(request.user)
            .prefetch_related("athletes__person")
            .filter(pk=pk)
            .first()
        )
        if not group:
            return JsonResponse({"error": "not_found"}, status=404)

        try:
            payload = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "invalid_payload"}, status=400)

        action = payload.get("action")
        athlete_ids = payload.get("athlete_ids")
        if action not in {"add", "remove"} or not isinstance(athlete_ids, list):
            return JsonResponse({"error": "invalid_request"}, status=400)

        try:
            athlete_ids = [int(athlete_id) for athlete_id in athlete_ids]
        except (TypeError, ValueError):
            return JsonResponse({"error": "invalid_athlete_ids"}, status=400)

        allowed_athletes = set(
            get_athlete_queryset_for_user(request.user).values_list("id", flat=True)
        )
        valid_ids = [athlete_id for athlete_id in athlete_ids if athlete_id in allowed_athletes]

        if not valid_ids:
            members_data = _serialize_group_members(group)
            return JsonResponse({"success": True, "members": members_data})

        if action == "add":
            group.athletes.add(*valid_ids)
        elif action == "remove":
            group.athletes.remove(*valid_ids)

        members_data = _serialize_group_members(group)
        return JsonResponse({"success": True, "members": members_data})


def _serialize_group_members(group: Group) -> list[dict[str, object]]:
    members = group.athletes.select_related("person").order_by(
        "person__surname", "person__name"
    )
    serialized: list[dict[str, object]] = []
    for athlete in members:
        person = athlete.person
        full_name = ""
        if person:
            full_name = " ".join(
                part
                for part in (person.surname, person.name, person.middlename)
                if part
            ).strip()
        serialized.append(
            {
                "id": athlete.id,
                "full_name": full_name or f"Спортсмен #{athlete.id}",
                "level": athlete.level or "",
                "rank": athlete.rank or "",
            }
        )
    return serialized
