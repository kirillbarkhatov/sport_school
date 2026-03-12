from django import forms

from api.models import extract_source_id
from school.models import Competition


class StreamRunLaunchForm(forms.Form):
    protocol_link = forms.CharField(
        max_length=2048,
        label="Ссылка на онлайн-результаты",
        help_text="Google Drive/Sheets ссылка или ID файла.",
    )
    competition = forms.ModelChoiceField(
        queryset=Competition.objects.none(),
        required=False,
        label="Соревнование",
        empty_label="Без привязки",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["protocol_link"].widget.attrs.update(
            {
                "class": "form-control",
                "placeholder": "https://docs.google.com/spreadsheets/d/...",
            }
        )
        self.fields["competition"].queryset = Competition.objects.order_by("-start_date", "-date", "-id")
        self.fields["competition"].widget.attrs.update({"class": "form-select"})

    def clean_protocol_link(self) -> str:
        value = (self.cleaned_data.get("protocol_link") or "").strip()
        if not value:
            raise forms.ValidationError("Укажите ссылку или ID протокола.")
        source_id = extract_source_id(value)
        if not source_id:
            raise forms.ValidationError("Не удалось определить источник протокола по ссылке.")
        return value
