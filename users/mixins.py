"""Миксины и декораторы доступа. Решения принимает единый слой прав organizations.permissions."""

from functools import wraps

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse

from organizations.alumni import can_read_history, history_family_ids
from organizations.permissions import authorize, has_site_access


class ApprovedUserRequiredMixin(LoginRequiredMixin):
    """Вошёл и подтверждён (legacy: is_approved или staff; memberships: активное членство).

    history_read_allowed = True — страница собственной истории: выпускник (право history.read) проходит
    только на чтение с request.history_only = True, а представление ограничивает записи своими семьями
    (restrict_to_history). Остальные страницы клуба выпускнику закрыты (D17).
    """

    history_read_allowed = False

    def dispatch(self, request, *args, **kwargs):
        request.history_only = False
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not has_site_access(request.user):
            if can_read_history(request.user):
                if self.history_read_allowed and request.method in ("GET", "HEAD"):
                    request.history_only = True
                    return super().dispatch(request, *args, **kwargs)
                return redirect(reverse("members:family_list"))  # выпускнику — к своей истории
            return redirect(reverse("users:awaiting_approval"))
        return super().dispatch(request, *args, **kwargs)

    def restrict_to_history(self, queryset):
        if getattr(self.request, "history_only", False):
            return queryset.filter(pk__in=history_family_ids(self.request.user))
        return queryset


class StaffAreaRequiredMixin(ApprovedUserRequiredMixin):
    """Зона сотрудников (действие staff.area): изменения соревнований, групп, занятий, документы, AI, выгрузки."""

    staff_action = "staff.area"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and has_site_access(request.user):
            if not authorize(request.user, self.staff_action):
                return HttpResponseForbidden("Недостаточно прав.")
        return super().dispatch(request, *args, **kwargs)


def staff_area_required(view):
    """То же для функций: гость — на вход, неподтверждённый — в ожидание, без роли сотрудника — 403."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        user = request.user
        if not user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not has_site_access(user):
            return redirect(reverse("users:awaiting_approval"))
        if not authorize(user, "staff.area"):
            return HttpResponseForbidden("Недостаточно прав.")
        return view(request, *args, **kwargs)

    return wrapper
