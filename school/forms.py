from django import forms
from django.forms import BooleanField, inlineformset_factory

from .models import (
    Athlete,
    Person,
    Family,
    FamilyMember,
    Class,
    ClassEnrollment,
    Group,
)


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
        fields = ["contact_person", "comment"]


class FamilyMemberForm(forms.ModelForm):
    class Meta:
        model = FamilyMember
        fields = ["person", "relation"]
