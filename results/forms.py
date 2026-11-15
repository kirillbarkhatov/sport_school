import json

from django import forms

from bot.models import TelegramChat
from school.forms import StyleFormMixin

from .core.parsing import ColumnMap
from .models import ResultsEvent
from .sources import SourceConfigError, extract_file_id


class EventForm(StyleFormMixin, forms.ModelForm):
    class Meta:
        model = ResultsEvent
        fields = (
            "title",
            "competition",
            "source_kind",
            "source_ref",
            "source_file",
            "poll_interval_sec",
            "idle_interval_sec",
            "finalize_timeout_sec",
            "finalize_max_missing",
            "highlight_club",
            "public_enabled",
            "telegram_enabled",
            "telegram_channel",
        )
        help_texts = {
            "source_ref": "Ссылка на Google Таблицу или XLSX на Drive. Сервисному аккаунту нужен доступ «Читатель».",
            "highlight_club": "Строки этого клуба подсвечиваются в таблицах (по вхождению, без учёта регистра).",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["telegram_channel"].queryset = TelegramChat.objects.filter(
            type__in=[TelegramChat.ChatType.CHANNEL, TelegramChat.ChatType.SUPERGROUP, TelegramChat.ChatType.GROUP]
        ).order_by("title")

    def clean(self):
        data = super().clean()
        kind = data.get("source_kind")
        if kind in (ResultsEvent.SourceKind.GOOGLE_SHEET, ResultsEvent.SourceKind.DRIVE_XLSX):
            try:
                extract_file_id(data.get("source_ref") or "")
            except SourceConfigError as exc:
                self.add_error("source_ref", str(exc))
        if kind == ResultsEvent.SourceKind.UPLOAD and not (data.get("source_file") or self.instance.source_file):
            self.add_error("source_file", "Загрузите файл протокола (XLSX)")
        if data.get("telegram_enabled") and not data.get("telegram_channel"):
            self.add_error("telegram_channel", "Выберите канал для публикации")
        return data


class ColumnMappingsForm(forms.Form):
    mappings = forms.CharField(
        label="Разметка колонок (JSON)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 10, "class": "form-control font-monospace", "spellcheck": "false"}),
        help_text="Пусто — автоопределение. Индексы колонок с нуля, header_row — номер строки шапки с единицы.",
    )

    def clean_mappings(self):
        raw = (self.cleaned_data.get("mappings") or "").strip()
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError(f"Некорректный JSON: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise forms.ValidationError("Ожидается объект {лист: разметка}")
        result = {}
        for sheet, mapping in data.items():
            if not isinstance(mapping, dict):
                raise forms.ValidationError(f"Лист «{sheet}»: ожидается объект")
            try:
                cmap = ColumnMap.from_dict(mapping)
            except TypeError as exc:
                raise forms.ValidationError(
                    f"Лист «{sheet}»: нужны start_number, club, run1 и имя (full_name или surname+first_name)"
                ) from exc
            if cmap.full_name is None and (cmap.surname is None or cmap.first_name is None):
                raise forms.ValidationError(f"Лист «{sheet}»: укажите full_name или surname и first_name")
            result[sheet] = cmap.to_dict()
        return result


class ManualResultForm(forms.Form):
    run1 = forms.CharField(label="1 заезд", required=False, max_length=32)
    run2 = forms.CharField(label="2 заезд", required=False, max_length=32)
    reset = forms.BooleanField(required=False)
