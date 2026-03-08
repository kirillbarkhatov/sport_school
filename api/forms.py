from django import forms

from bot.models import TelegramChat, TelegramParticipant


class StreamRunLaunchForm(forms.Form):
    protocol_link = forms.CharField(
        max_length=2048,
        label="Ссылка на онлайн-результаты",
        help_text="Google Drive/Sheets ссылка или ID файла.",
    )
    telegram_publish_enabled = forms.BooleanField(
        required=False,
        label="Публиковать результаты в Telegram-канал",
    )
    telegram_channel = forms.ModelChoiceField(
        required=False,
        queryset=TelegramChat.objects.none(),
        label="Канал для публикации",
        empty_label="Выберите канал",
        help_text="Доступны только каналы/супергруппы, где бот является администратором.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["protocol_link"].widget.attrs.update(
            {
                "class": "form-control",
                "placeholder": "https://docs.google.com/spreadsheets/d/...",
            }
        )
        self.fields["telegram_channel"].queryset = (
            TelegramChat.objects.filter(
                type__in=[TelegramChat.ChatType.CHANNEL, TelegramChat.ChatType.SUPERGROUP],
                participants__is_bot=True,
                participants__status__in=[
                    TelegramParticipant.MemberStatus.ADMIN,
                    TelegramParticipant.MemberStatus.CREATOR,
                ],
            )
            .order_by("title", "chat_id")
            .distinct()
        )
        self.fields["telegram_publish_enabled"].widget.attrs.update({"class": "form-check-input"})
        self.fields["telegram_channel"].widget.attrs.update({"class": "form-select"})

    def clean(self):
        cleaned_data = super().clean()
        publish_enabled = bool(cleaned_data.get("telegram_publish_enabled"))
        telegram_channel = cleaned_data.get("telegram_channel")
        if publish_enabled and telegram_channel is None:
            self.add_error("telegram_channel", "Выберите канал для публикации.")
        return cleaned_data
