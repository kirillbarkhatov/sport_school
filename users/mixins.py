"""Миксины и декораторы доступа. Решения принимает единый слой прав organizations.permissions."""

from functools import wraps

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import reverse

from organizations.permissions import authorize, has_site_access


class ApprovedUserRequiredMixin(LoginRequiredMixin):
    """Вошёл и подтверждён (legacy: is_approved или staff; memberships: активное членство)."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not has_site_access(request.user):
            return redirect(reverse("users:awaiting_approval"))
        return super().dispatch(request, *args, **kwargs)


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
