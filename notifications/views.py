from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView

from .forms import NotificationForm
from .models import Notification
from notifications.services import deliver_notification
from users.mixins import ApprovedUserRequiredMixin


class NotificationListView(ApprovedUserRequiredMixin, ListView):
    model = Notification
    template_name = "notifications/notification_list.html"


class NotificationCreateView(ApprovedUserRequiredMixin, CreateView):
    model = Notification
    form_class = NotificationForm
    template_name = "notifications/notification_form.html"
    success_url = reverse_lazy("notifications:notification_list")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        notification = self.object
        deliver_notification(notification)
        return response
