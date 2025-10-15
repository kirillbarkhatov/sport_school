from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import CreateView, ListView

from .forms import NotificationForm
from .models import Notification
from asgiref.sync import async_to_sync
from telegram import Bot
from django.core.mail import send_mail
from config.settings import BOT_TOKEN, DEFAULT_FROM_EMAIL


class NotificationListView(LoginRequiredMixin, ListView):
    model = Notification
    template_name = "notifications/notification_list.html"


class NotificationCreateView(LoginRequiredMixin, CreateView):
    model = Notification
    form_class = NotificationForm
    template_name = "notifications/notification_form.html"
    success_url = reverse_lazy("notifications:notification_list")

    def form_valid(self, form):
        response = super().form_valid(form)
        notification = self.object
        if notification.send_to_telegram:
            bot = Bot(token=BOT_TOKEN)
            for user in notification.users.exclude(tg_id__isnull=True):
                async_to_sync(bot.send_message)(
                    chat_id=user.tg_id, text=notification.message
                )
        if notification.send_to_email:
            recipients = [u.email for u in notification.users.exclude(email="")]
            if recipients:
                send_mail(
                    notification.title,
                    notification.message,
                    DEFAULT_FROM_EMAIL,
                    recipients,
                )
        return response
