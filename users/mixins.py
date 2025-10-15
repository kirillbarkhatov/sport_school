from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect
from django.urls import reverse


class ApprovedUserRequiredMixin(LoginRequiredMixin):
    """Mixin для проверки подтверждения пользователя."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)

        if request.user.is_staff or request.user.is_superuser:
            return super().dispatch(request, *args, **kwargs)

        if not request.user.is_approved:
            return redirect(reverse("users:awaiting_approval"))

        return super().dispatch(request, *args, **kwargs)
