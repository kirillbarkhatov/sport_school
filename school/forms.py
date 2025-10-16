from django import forms
from django.forms import BooleanField, BaseFormSet, formset_factory, inlineformset_factory

from django.utils import timezone

from .constants import DEFAULT_EQUIPMENT, DEFAULT_LOCATIONS, DEFAULT_TRAINING_TYPES
from .models import (
    Athlete,
    Person,
    Family,
    FamilyMember,
    FamilyAthleteProfile,
    FamilyService,
    FamilyPayment,
    AthleteContract,
    Class,
    ClassEnrollment,
    Group,
)
from .services import compute_contract_defaults


class StyleFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            if isinstance(field, BooleanField):
                field.widget.attrs["class"] = "form-check-input"
            else:
                field.widget.attrs["class"] = "form-control"


class HorizontalFormMixin:
    """
    Миксин для горизонтального выравнивания полей формы
    """

    # def __init__(self, *args, **kwargs):
    #     super().__init__(*args, **kwargs)
    #     for field_name, field in self.fields.items():
    #         # Добавляем класс для горизонтального размещения полей
    #         field.widget.attrs['class'] = 'form-control-inline'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = "form-control"


class AthleteForm(StyleFormMixin, forms.ModelForm):
    class Meta:
        model = Athlete
        fields = ["level", "rank", "medical_certificate", "comment"]


class PersonForm(StyleFormMixin, forms.ModelForm):
    class Meta:
        model = Person
        fields = "__all__"  # Выберите нужные поля


class ClassForm(StyleFormMixin, forms.ModelForm):
    equipment = forms.MultipleChoiceField(
        label="Необходимое снаряжение",
        required=False,
        choices=[(item, item) for item in DEFAULT_EQUIPMENT],
        widget=forms.SelectMultiple(attrs={"size": 7}),
        help_text="Выберите всё, что спортсменам нужно взять с собой",
    )

    class Meta:
        model = Class
        fields = [
            "date",
            "duration",
            "location",
            "training_type",
            "equipment",
            "group",
            "type",
            "comment",
        ]
        widgets = {
            "date": forms.DateTimeInput(
                attrs={"type": "datetime-local", "placeholder": "2024-11-07T17:30"}
            ),
            "duration": forms.NumberInput(attrs={"min": 0, "step": 5}),
            "comment": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        equipment_field = self.fields["equipment"]
        equipment_field.widget.attrs["class"] = "form-select"

        # Add dynamic equipment options so existing custom values pass validation.
        current_equipment = list(self.instance.equipment or [])
        known_values = list(DEFAULT_EQUIPMENT)
        for value in current_equipment:
            if value not in known_values:
                known_values.append(value)
        equipment_field.choices = [(item, item) for item in known_values]
        if not self.is_bound:
            equipment_field.initial = current_equipment

        # Suggest common values via datalists while allowing custom input.
        self.fields["location"].widget.attrs.setdefault("list", "location-options")
        self.fields["training_type"].widget.attrs.setdefault(
            "list", "training-type-options"
        )

        if not self.is_bound and self.instance.pk and self.instance.date:
            localized = timezone.localtime(self.instance.date)
            self.initial["date"] = localized.strftime("%Y-%m-%dT%H:%M")


class GroupForm(StyleFormMixin, forms.ModelForm):
    class Meta:
        model = Group
        fields = "__all__"


class AthleteSelectionForm(forms.Form):
    """Форма для выбора спортсменов"""

    athletes = forms.ModelMultipleChoiceField(
        queryset=Athlete.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Выберите спортсменов",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from users.utils import get_athlete_queryset_for_user

        qs = get_athlete_queryset_for_user(user) if user else Athlete.objects.all()
        self.fields["athletes"].queryset = qs.order_by("person__surname")


class FamilyForm(StyleFormMixin, forms.ModelForm):
    class Meta:
        model = Family
        fields = [
            "family_name",
            "contact_person",
            "status",
            "base_monthly_fee",
            "discount_type",
            "discount_value",
            "current_month_paid",
            "comment",
        ]
        widgets = {
            "comment": forms.Textarea(attrs={"rows": 3}),
        }


class FamilyMemberForm(forms.ModelForm):
    class Meta:
        model = FamilyMember
        fields = ["person", "relation"]


class FamilyMemberInlineForm(forms.Form):
    existing_person = forms.ModelChoiceField(
        queryset=Person.objects.all().order_by("surname"),
        required=False,
        label="Существующий участник",
    )
    surname = forms.CharField(required=False, label="Фамилия")
    name = forms.CharField(required=False, label="Имя")
    middlename = forms.CharField(required=False, label="Отчество", widget=forms.TextInput())
    date_of_birth = forms.DateField(required=False, label="Дата рождения", widget=forms.DateInput(attrs={"type": "date"}))
    gender = forms.ChoiceField(required=False, choices=Person.GENDER_CHOICES, label="Пол")
    relation = forms.ChoiceField(choices=FamilyMember.FAMILY_RELATION, label="Отношение", required=False)
    is_athlete = forms.BooleanField(required=False, label="Создать как спортсмена")

    def clean(self):
        cleaned = super().clean()
        person = cleaned.get("existing_person")
        surname = cleaned.get("surname")
        name = cleaned.get("name")
        if not any(cleaned.values()):
            return cleaned
        if not person and not (surname and name):
            raise forms.ValidationError("Выберите существующего участника или заполните фамилию и имя.")
        if (person or surname or name) and not cleaned.get("relation"):
            raise forms.ValidationError("Укажите отношение внутри семьи.")
        return cleaned


class FamilyAthleteProfileForm(StyleFormMixin, forms.ModelForm):
    class Meta:
        model = FamilyAthleteProfile
        fields = [
            "contract_active",
            "monthly_fee",
            "discount_type",
            "discount_value",
            "current_month_paid",
            "notes",
        ]

FamilyMemberInlineFormSet = formset_factory(FamilyMemberInlineForm, extra=2, can_delete=False)


ExistingFamilyMemberFormSet = inlineformset_factory(
    Family,
    FamilyMember,
    form=FamilyMemberForm,
    extra=1,
    can_delete=True,
)


FamilyAthleteProfileFormSet = inlineformset_factory(
    Family,
    FamilyAthleteProfile,
    form=FamilyAthleteProfileForm,
    extra=0,
    can_delete=False,
)


class FamilyServiceForm(StyleFormMixin, forms.ModelForm):
    profile = forms.ModelChoiceField(
        queryset=FamilyAthleteProfile.objects.none(),
        required=False,
        label="Спортсмен",
    )

    def __init__(self, *args, family=None, **kwargs):
        super().__init__(*args, **kwargs)
        if family:
            self.fields["profile"].queryset = FamilyAthleteProfile.objects.filter(family=family).select_related("athlete__person")
        else:
            self.fields["profile"].queryset = FamilyAthleteProfile.objects.none()
        self.fields["profile"].empty_label = "—"
        self.fields["profile"].label_from_instance = lambda obj: f"{obj.athlete.person.surname} {obj.athlete.person.name}"
        self.fields["profile"].widget.attrs.update({"data-profile-select": "true"})
        self.fields["service_type"].widget.attrs.update({"data-service-type": "true"})
        name_widget = self.fields["name"].widget
        name_widget.attrs.update({"data-service-name": "true", "readonly": "readonly"})

    class Meta:
        model = FamilyService
        fields = [
            "profile",
            "name",
            "service_type",
            "amount",
            "discount_type",
            "discount_value",
            "is_recurring",
            "due_date",
            "notes",
        ]
        widgets = {
            "notes": forms.Textarea(attrs={"rows": 2}),
            "due_date": forms.DateInput(attrs={"type": "date"}),
        }


class FamilyPaymentForm(StyleFormMixin, forms.ModelForm):
    service = forms.ModelChoiceField(
        queryset=FamilyService.objects.none(),
        label="Услуга",
    )

    def __init__(self, *args, family=None, **kwargs):
        super().__init__(*args, **kwargs)
        if family:
            self.fields["service"].queryset = FamilyService.objects.filter(family=family)
        else:
            self.fields["service"].queryset = FamilyService.objects.none()

    class Meta:
        model = FamilyPayment
        fields = ["service", "amount", "payment_type", "note"]
        widgets = {
            "note": forms.Textarea(attrs={"rows": 2}),
        }


class AthleteContractForm(StyleFormMixin, forms.ModelForm):
    def __init__(self, *args, profile=None, **kwargs):
        instance = kwargs.get("instance")
        if instance is not None and profile is None:
            profile = instance.profile
        if profile is not None and not isinstance(profile, FamilyAthleteProfile):
            profile = getattr(profile, "profile", profile)
        self.profile = profile
        super().__init__(*args, **kwargs)
        self.fields["number"].widget.attrs["placeholder"] = "Авто"
        if not (self.instance and self.instance.pk):
            defaults = compute_contract_defaults()
            for field, value in defaults.items():
                if field in self.fields and field not in self.initial:
                    self.initial[field] = value
                    self.fields[field].initial = value
            if self.profile:
                next_number = _next_contract_number(self.profile.family)
                if next_number and "number" in self.fields and "number" not in self.initial:
                    self.initial["number"] = next_number
                    self.fields["number"].initial = next_number
        for field in ["issue_date", "start_date", "end_date"]:
            if field in self.fields:
                self.fields[field].widget.attrs.setdefault("data-contract-date", "true")
                value = self.initial.get(field, self.fields[field].initial)
                if value:
                    iso_value = value.isoformat() if hasattr(value, "isoformat") else str(value)
                    self.fields[field].widget.attrs["data-default-date"] = iso_value

    class Meta:
        model = AthleteContract
        fields = [
            "number",
            "issue_date",
            "start_date",
            "end_date",
            "base_fee",
            "discount_value",
        ]
        widgets = {
            "issue_date": forms.DateInput(attrs={"type": "date"}),
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
        }


def _next_contract_number(family):
    last = (
        AthleteContract.objects.filter(profile__family=family)
        .order_by("-created_at")
        .first()
    )
    if last and last.number and last.number.isdigit():
        return str(int(last.number) + 1)
    return ""
