from django import forms
from django.forms import BooleanField, BaseFormSet, formset_factory, inlineformset_factory

from django.utils import timezone

from .choices import TrainingEquipment, TrainingKind, TrainingLocation
from .training_rules import (
    apply_training_rules,
    get_allowed_equipment,
    get_allowed_training_for_location,
    get_default_equipment_for_training,
    normalize_training_selection,
)
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
    Competition,
    Club,
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


class AthleteCompactForm(StyleFormMixin, forms.ModelForm):
    main_group = forms.ModelChoiceField(
        queryset=Group.objects.none(), required=False, label=""
    )

    class Meta:
        model = Athlete
        fields = ["rank", "level"]
        widgets = {
            "rank": forms.Select(attrs={"class": "form-select"}),
            "level": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, group_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = group_qs if group_qs is not None else Group.objects.all()
        self.fields["main_group"].queryset = qs.order_by("name")
        self.fields["main_group"].widget.attrs.setdefault("class", "form-select")
        if self.instance and self.instance.pk:
            current = self.instance.groups_athletes.first()
            if current:
                self.initial.setdefault("main_group", current)

    def save(self, commit=True):
        athlete = super().save(commit)
        main_group = self.cleaned_data.get("main_group")
        if commit:
            if main_group:
                athlete.groups_athletes.set([main_group])
            else:
                athlete.groups_athletes.clear()
        else:
            # if commit=False, postpone group assignment to caller
            self._pending_main_group = main_group
        return athlete


class PersonForm(StyleFormMixin, forms.ModelForm):
    class Meta:
        model = Person
        fields = "__all__"
        widgets = {
            "date_of_birth": forms.DateInput(
                format="%Y-%m-%d",
                attrs={
                    "type": "date",
                    "class": "form-control",
                }
            ),
            "gender": forms.Select(attrs={"class": "form-select"}),
            "comment": forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # StyleFormMixin sets baseline classes; adjust select/input specifics here.
        if "gender" in self.fields:
            self.fields["gender"].widget.attrs["class"] = "form-select"
        if "photo" in self.fields:
            self.fields["photo"].widget.attrs.setdefault("class", "form-control")
        if "date_of_birth" in self.fields:
            self.fields["date_of_birth"].required = False
            self.fields["date_of_birth"].input_formats = ["%Y-%m-%d"]
            if self.instance and self.instance.date_of_birth:
                self.initial.setdefault(
                    "date_of_birth", self.instance.date_of_birth.strftime("%Y-%m-%d")
                )


class PersonCompactForm(StyleFormMixin, forms.ModelForm):
    class Meta:
        model = Person
        fields = ["surname", "name", "middlename", "date_of_birth"]
        widgets = {
            "surname": forms.TextInput(attrs={"placeholder": "Фамилия"}),
            "name": forms.TextInput(attrs={"placeholder": "Имя"}),
            "middlename": forms.TextInput(attrs={"placeholder": "Отчество"}),
            "date_of_birth": forms.DateInput(
                format="%Y-%m-%d",
                attrs={"type": "date", "class": "form-control"},
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "date_of_birth" in self.fields:
            self.fields["date_of_birth"].required = False
            self.fields["date_of_birth"].input_formats = ["%Y-%m-%d"]
            if self.instance and self.instance.date_of_birth:
                self.initial.setdefault(
                    "date_of_birth", self.instance.date_of_birth.strftime("%Y-%m-%d")
                )


class ClassForm(StyleFormMixin, forms.ModelForm):
    equipment = forms.MultipleChoiceField(
        label="Необходимое снаряжение",
        required=False,
        choices=TrainingEquipment.choices,
        widget=forms.SelectMultiple(attrs={"size": 7, "class": "form-select"}),
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

        if self.instance and getattr(self.instance, "pk", None):
            apply_training_rules(self.instance)

        equipment_field = self.fields["equipment"]

        # Add dynamic equipment options so existing custom values pass validation.
        current_equipment = list(self.instance.equipment or [])
        known_choices = list(TrainingEquipment.choices)
        known_values = {value for value, _ in known_choices}
        dynamic_choices = list(known_choices)
        for value in current_equipment:
            if value not in known_values:
                dynamic_choices.append((value, value))
        equipment_field.choices = dynamic_choices
        if not self.is_bound:
            equipment_field.initial = current_equipment

        location_field = self.fields["location"]
        location_choices = list(TrainingLocation.choices)
        if not getattr(self.instance, "pk", None):
            location_field.choices = [("", "Выберите локацию")] + location_choices
            if not self.is_bound:
                self.initial.setdefault("location", "")
        else:
            location_field.choices = location_choices
        self.fields["training_type"].choices = TrainingKind.choices
        if "group" in self.fields:
            self.fields["group"].required = False
            self.fields["group"].empty_label = "—"
        for field_name in ("location", "training_type", "type", "group"):
            if field_name in self.fields:
                self.fields[field_name].widget.attrs["class"] = "form-select"

        if not self.is_bound and self.instance.pk and self.instance.date:
            localized = timezone.localtime(self.instance.date)
            self.initial["date"] = localized.strftime("%Y-%m-%dT%H:%M")

        bound_data = self.data if self.is_bound else None
        selected_location = (
            (bound_data.get("location") if bound_data else None)
            or self.initial.get("location")
            or getattr(self.instance, "location", None)
        )

        training_label_map = dict(TrainingKind.choices)
        current_training = (
            (bound_data.get("training_type") if bound_data else None)
            or self.initial.get("training_type")
            or getattr(self.instance, "training_type", None)
        )

        allowed_training = get_allowed_training_for_location(selected_location)
        if allowed_training is not None:
            training_choices = [
                (value, label)
                for value, label in TrainingKind.choices
                if value in allowed_training
            ]
            existing_values = {value for value, _ in training_choices}
            if current_training and current_training not in existing_values:
                training_choices.append(
                    (current_training, training_label_map.get(current_training, current_training))
                )
            if training_choices:
                self.fields["training_type"].choices = training_choices

        allowed_equipment = get_allowed_equipment(selected_location, current_training)
        if allowed_equipment is not None:
            allowed_set = set(allowed_equipment)
            filtered_choices: list[tuple[str, str]] = []
            for value, label in equipment_field.choices:
                if value in allowed_set or value in current_equipment:
                    filtered_choices.append((value, label))
            if filtered_choices:
                equipment_field.choices = filtered_choices

            if not self.is_bound and not current_equipment and allowed_set:
                default_candidates = [
                    value
                    for value in get_default_equipment_for_training(current_training)
                    if value in allowed_set
                ]
                if not default_candidates:
                    ordered_allowed = [
                        value for value, _ in TrainingEquipment.choices if value in allowed_set
                    ]
                    default_candidates = ordered_allowed[:1]
                if default_candidates:
                    equipment_field.initial = default_candidates

    def clean(self):
        cleaned_data = super().clean()
        location = cleaned_data.get("location") or getattr(self.instance, "location", None)
        training_type = cleaned_data.get("training_type") or getattr(self.instance, "training_type", None)
        equipment = cleaned_data.get("equipment") or []
        class_type = cleaned_data.get("type") or getattr(self.instance, "type", None)
        group = cleaned_data.get("group")
        if class_type == "regular" and not group:
            self.add_error("group", "Для регулярного занятия нужно выбрать группу.")
        location, training_type, normalized_equipment = normalize_training_selection(
            location,
            training_type,
            equipment,
        )
        cleaned_data["location"] = location
        cleaned_data["training_type"] = training_type
        cleaned_data["equipment"] = normalized_equipment
        return cleaned_data


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
            "comment",
        ]
        widgets = {
            "comment": forms.Textarea(attrs={"rows": 3}),
        }


class FamilyMemberForm(forms.ModelForm):
    class Meta:
        model = FamilyMember
        fields = ["person", "relation"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        is_placeholder = bool(getattr(self.instance, "pk", None) and not getattr(self.instance, "person_id", None))
        self.fields["person"].required = not is_placeholder


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
                self.fields[field].input_formats = ["%Y-%m-%d"]
                self.fields[field].widget = forms.DateInput(
                    attrs={"type": "date", "data-contract-date": "true"},
                    format="%Y-%m-%d",
                )
                value = None
                if field in self.initial:
                    value = self.initial[field]
                elif self.instance and getattr(self.instance, field, None):
                    value = getattr(self.instance, field)
                elif self.fields[field].initial:
                    value = self.fields[field].initial
                if value and not self.is_bound:
                    iso_value = value.strftime("%Y-%m-%d") if hasattr(value, "strftime") else str(value)
                    self.initial[field] = iso_value
                    self.fields[field].initial = iso_value
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
            "issue_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }


class CompetitionForm(StyleFormMixin, forms.ModelForm):
    class Meta:
        model = Competition
        fields = ["name", "start_date", "end_date", "location", "description"]
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "end_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "description": forms.Textarea(attrs={"rows": 3}),
        }


class ClubForm(StyleFormMixin, forms.ModelForm):
    class Meta:
        model = Club
        fields = ["name"]


def _next_contract_number(family):
    last = (
        AthleteContract.objects.filter(profile__family=family)
        .order_by("-created_at")
        .first()
    )
    if last and last.number and last.number.isdigit():
        return str(int(last.number) + 1)
    return ""
