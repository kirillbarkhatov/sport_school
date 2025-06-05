from django import forms

from .models import Notification


class NotificationForm(forms.ModelForm):
    class Meta:
        model = Notification
        fields = ["title", "message", "users", "send_to_telegram", "send_to_email"]
        widgets = {
            "users": forms.CheckboxSelectMultiple,
        }
