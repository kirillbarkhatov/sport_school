from decimal import Decimal

from django import forms
from django.forms import BooleanField, BaseFormSet, formset_factory, inlineformset_factory
from django.utils import timezone

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
from .services import get_season_bounds


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
    class Meta:
        model = Class
        fields = "__all__"  # Выберите нужные поля


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


class AthleteContractBaseForm(StyleFormMixin, forms.ModelForm):
    def __init__(self, *args, profile=None, **kwargs):
        self.profile = profile or kwargs.get("instance", None)
        if self.profile and not isinstance(self.profile, FamilyAthleteProfile):
            self.profile = self.profile.profile
        super().__init__(*args, **kwargs)
        today = timezone.now().date()
        july_first = today.replace(month=7, day=1)
        if today < july_first:
            issue_default = today
            start_default = today
        else:
            issue_default = today.replace(month=9, day=1)
            start_default = issue_default
        if today.month in (7, 8):
            end_default = start_default.replace(year=start_default.year + 1, month=8, day=31)
        else:
            _, end_default = get_season_bounds(today)

        if self.profile:
            if not self.instance.pk:
                self.initial.setdefault("issue_date", issue_default)
                self.initial.setdefault("start_date", start_default)
                self.initial.setdefault("end_date", end_default)
                self.initial.setdefault("base_fee", Decimal("12000.00"))
                self.initial.setdefault("discount_value", Decimal("0.00"))
                if not self.initial.get("number"):
                    last = athlete_contract_number_seed(self.profile.family)
                    if last:
                        self.initial["number"] = last
        else:
            # при редактировании данные подхватываются из instance; ничего не меняем
            pass

        self.fields["number"].widget.attrs["placeholder"] = "Авто"

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


class AthleteContractCreateForm(AthleteContractBaseForm):
    pass


class AthleteContractUpdateForm(AthleteContractBaseForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # при редактировании используем данные из instance, поэтому сбрасываем initial
        if self.instance and self.instance.pk:
            self.initial.setdefault("number", self.instance.number)
            self.initial.setdefault("issue_date", self.instance.issue_date)
            self.initial.setdefault("start_date", self.instance.start_date)
            self.initial.setdefault("end_date", self.instance.end_date)
            self.initial.setdefault("base_fee", self.instance.base_fee)
            self.initial.setdefault("discount_value", self.instance.discount_value)
def athlete_contract_number_seed(family):
    last = AthleteContract.objects.filter(profile__family=family).order_by("-created_at").first()
    if last and last.number.isdigit():
        return str(int(last.number) + 1)
    return ""
