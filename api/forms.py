from django import forms


class StreamRunLaunchForm(forms.Form):
    protocol_link = forms.CharField(
        max_length=2048,
        label="Ссылка на онлайн-результаты",
        help_text="Google Drive/Sheets ссылка или ID файла.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["protocol_link"].widget.attrs.update(
            {
                "class": "form-control",
                "placeholder": "https://docs.google.com/spreadsheets/d/...",
            }
        )
