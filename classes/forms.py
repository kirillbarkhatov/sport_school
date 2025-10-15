from django import forms

from school.forms import StyleFormMixin
from users.models import User


class ClassNotificationForm(StyleFormMixin, forms.Form):
    title = forms.CharField(label="Тема", max_length=200)
    message = forms.CharField(label="Сообщение", widget=forms.Textarea)
    send_to_telegram = forms.BooleanField(label="Отправить в Telegram", required=False, initial=True)
    send_to_email = forms.BooleanField(label="Отправить на email", required=False, initial=False)
    recipients = forms.ModelMultipleChoiceField(
        label="Получатели",
        queryset=User.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=True,
    )

    def __init__(self, *args, recipients_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = recipients_queryset if recipients_queryset is not None else User.objects.none()
        self.fields["recipients"].queryset = qs.order_by("last_name", "first_name")
        if not qs.exists():
            self.fields["recipients"].help_text = "Нет пользователей, связанных с выбранными спортсменами"
