from datetime import date
from decimal import Decimal

from django.urls import reverse_lazy, reverse
from django.views.generic import ListView, DetailView, CreateView, UpdateView, DeleteView

from users.mixins import ApprovedUserRequiredMixin
from users.utils import get_person_queryset_for_user
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect

from school.forms import (
    PersonForm,
    FamilyForm,
    FamilyMemberInlineFormSet,
    ExistingFamilyMemberFormSet,
    FamilyAthleteProfileFormSet,
    FamilyServiceForm,
    FamilyPaymentForm,
)
from school.models import Person, Family, FamilyMember, Athlete, FamilyAthleteProfile, FamilyService, FamilyPayment
from school.models import DiscountType, ServiceType

# CRUD для модели "Person"
class PersonListView(ApprovedUserRequiredMixin, ListView):
    """Контроллер для работы с БД членов клуба - список"""

    model = Person
    template_name = "members/person_list.html"

    def get_queryset(self):
        return get_person_queryset_for_user(self.request.user).prefetch_related("familymember_set__family")


class PersonDetailView(ApprovedUserRequiredMixin, DetailView):
    """Контроллер для работы с БД членов клуба - инфо о персоне"""

    model = Person
    template_name = "members/person_detail.html"

    def get_queryset(self):
        return get_person_queryset_for_user(self.request.user).prefetch_related("familymember_set__family")


class PersonCreateView(ApprovedUserRequiredMixin, CreateView):
    """Контроллер для работы с БД членов клуба - создание"""

    model = Person
    template_name = "members/person_form.html"
    form_class = PersonForm

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


class PersonUpdateView(ApprovedUserRequiredMixin, UpdateView):
    """Контроллер для работы с БД членов клуба - изменение"""

    model = Person
    template_name = "members/person_form.html"
    form_class = PersonForm

    def get_queryset(self):
        return get_person_queryset_for_user(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


class PersonDeleteView(ApprovedUserRequiredMixin, DeleteView):
    """Контроллер для работы с БД членов клуба - удаление"""

    model = Person
    success_url = reverse_lazy("members:person_list")
    template_name = "members/person_confirm_delete.html"

    def get_queryset(self):
        return get_person_queryset_for_user(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)


class FamilyListView(ApprovedUserRequiredMixin, ListView):
    model = Family
    template_name = "members/family_list.html"

    def get_queryset(self):
        base_qs = Family.objects.prefetch_related("members__person", "users")
        if self.request.user.is_staff or self.request.user.is_superuser:
            return base_qs.order_by("family_name")
        family_ids = self.request.user.get_accessible_family_ids()
        return base_qs.filter(id__in=family_ids).order_by("family_name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        families = self.get_queryset()
        context["active_families"] = [f for f in families if f.status == Family.STATUS_ACTIVE]
        context["alumni_families"] = [f for f in families if f.status == Family.STATUS_ALUMNI]
        context["family_form"] = getattr(self, "family_form", FamilyForm())
        context["member_formset"] = getattr(
            self,
            "member_formset",
            FamilyMemberInlineFormSet(prefix="members"),
        )
        return context

    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()

        self.family_form = FamilyForm(request.POST)
        self.member_formset = FamilyMemberInlineFormSet(request.POST, prefix="members")

        if self.family_form.is_valid() and self.member_formset.is_valid():
            with transaction.atomic():
                family = self.family_form.save()
                self._create_members(family, self.member_formset)
            messages.success(request, "Семья успешно создана")
            return redirect("members:family_detail", pk=family.pk)

        return self.get(request, *args, **kwargs)

    def _create_members(self, family: Family, formset):
        for form in formset:
            if not form.cleaned_data or not any(form.cleaned_data.values()):
                continue
            person = form.cleaned_data.get("existing_person")
            if not person:
                person = Person.objects.create(
                    surname=form.cleaned_data.get("surname"),
                    name=form.cleaned_data.get("name"),
                    middlename=form.cleaned_data.get("middlename"),
                    date_of_birth=form.cleaned_data.get("date_of_birth") or date(1970, 1, 1),
                    gender=form.cleaned_data.get("gender") or Person.GENDER_CHOICES[0][0],
                )

            relation = form.cleaned_data.get("relation")
            member, created = FamilyMember.objects.get_or_create(
                family=family,
                person=person,
                defaults={"relation": relation},
            )
            if not created and relation:
                member.relation = relation
                member.save(update_fields=["relation"])

            if form.cleaned_data.get("is_athlete") and not hasattr(person, "athlete"):
                athlete = Athlete.objects.create(
                    person=person,
                    level=Athlete.LEVEL_CHOICES[0][0],
                )
            elif hasattr(person, "athlete"):
                athlete = person.athlete
            else:
                athlete = None

            if athlete:
                profile, created = FamilyAthleteProfile.objects.get_or_create(
                    family=family,
                    athlete=athlete,
                    defaults={
                        "monthly_fee": family.base_monthly_fee,
                        "discount_type": family.discount_type,
                        "discount_value": family.discount_value,
                    },
                )
                if created:
                    FamilyService.objects.create(
                        family=family,
                        profile=profile,
                        name=f"Ежемесячный платеж {athlete.person}",
                        service_type=ServiceType.MONTHLY,
                        amount=profile.monthly_fee,
                        discount_type=profile.discount_type,
                        discount_value=profile.discount_value,
                        is_recurring=True,
                    )


class FamilyDetailView(ApprovedUserRequiredMixin, DetailView):
    model = Family
    template_name = "members/family_detail.html"

    def get_queryset(self):
        base = Family.objects.prefetch_related("members__person", "users")
        if self.request.user.is_staff or self.request.user.is_superuser:
            return base
        return base.filter(id__in=self.request.user.get_accessible_family_ids())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        family: Family = self.object
        # ensure profiles exist for семейные спортсмены
        for membership in family.members.select_related("person"):
            person = membership.person
            if hasattr(person, "athlete"):
                profile, created = FamilyAthleteProfile.objects.get_or_create(
                    family=family,
                    athlete=person.athlete,
                    defaults={
                        "monthly_fee": family.base_monthly_fee,
                        "discount_type": family.discount_type,
                        "discount_value": family.discount_value,
                    },
                )
                if created or not profile.services.filter(service_type=ServiceType.MONTHLY).exists():
                    FamilyService.objects.create(
                        family=family,
                        profile=profile,
                        name=f"Ежемесячный платеж {profile.athlete.person}",
                        service_type=ServiceType.MONTHLY,
                        amount=profile.monthly_fee,
                        discount_type=profile.discount_type,
                        discount_value=profile.discount_value,
                        is_recurring=True,
                    )
        profiles = (
            FamilyAthleteProfile.objects.filter(family=family)
            .select_related("athlete__person")
            .prefetch_related("services__payments")
        )

        athlete_rows = []
        for profile in profiles:
            monthly_services = [s for s in profile.services.all() if s.service_type == ServiceType.MONTHLY and not s.is_closed]
            amount_due = Decimal("0.00")
            amount_paid = Decimal("0.00")
            for service in monthly_services:
                net_amount = service.amount - service.discount_value
                amount_due += net_amount
                amount_paid += sum((payment.amount for payment in service.payments.all()), Decimal("0.00"))
            balance = amount_due - amount_paid
            athlete_rows.append(
                {
                    "athlete": profile.athlete,
                    "monthly_fee": profile.monthly_fee,
                    "discount_type": profile.get_discount_type_display(),
                    "discount_value": profile.discount_value,
                    "contract_active": profile.contract_active,
                    "current_month_paid": profile.current_month_paid,
                    "balance": balance,
                }
            )

        services = (
            FamilyService.objects.filter(family=family)
            .select_related("profile__athlete__person")
            .prefetch_related("payments")
        )
        payments = FamilyPayment.objects.filter(service__family=family).select_related("service")
        service_rows = []
        for service in services:
            paid = sum((p.amount for p in service.payments.all()), Decimal("0.00"))
            net_amount = service.amount - service.discount_value
            service_rows.append(
                {
                    "service": service,
                    "net_amount": net_amount,
                    "paid_amount": paid,
                    "balance": net_amount - paid,
                }
            )

        context.update(
            athlete_rows=athlete_rows,
            service_rows=service_rows,
        )
        if self.request.user.is_staff:
            context["family_form"] = FamilyForm(instance=family)
            context["member_formset"] = ExistingFamilyMemberFormSet(instance=family, prefix="members")
            context["profile_formset"] = FamilyAthleteProfileFormSet(instance=family, prefix="profiles")
        return context


class FamilyUpdateView(ApprovedUserRequiredMixin, UpdateView):
    model = Family
    form_class = FamilyForm
    template_name = "members/family_form.html"
    success_url = reverse_lazy("members:family_list")

    def get_queryset(self):
        if self.request.user.is_staff or self.request.user.is_superuser:
            return Family.objects.all()
        return Family.objects.filter(id__in=self.request.user.get_accessible_family_ids())

    def get_success_url(self):
        return reverse("members:family_detail", kwargs={"pk": self.object.pk})

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.POST:
            context["member_formset"] = ExistingFamilyMemberFormSet(self.request.POST, instance=self.object, prefix="members")
            context["profile_formset"] = FamilyAthleteProfileFormSet(self.request.POST, instance=self.object, prefix="profiles")
        else:
            context["member_formset"] = ExistingFamilyMemberFormSet(instance=self.object, prefix="members")
            context["profile_formset"] = FamilyAthleteProfileFormSet(instance=self.object, prefix="profiles")
        return context

    def form_valid(self, form):
        context = self.get_context_data()
        member_formset = context["member_formset"]
        profile_formset = context["profile_formset"]
        if member_formset.is_valid() and profile_formset.is_valid():
            with transaction.atomic():
                self.object = form.save()
                member_formset.instance = self.object
                profile_formset.instance = self.object
                member_formset.save()
                profile_formset.save()
            messages.success(self.request, "Данные семьи обновлены")
            return redirect(self.get_success_url())
        return self.render_to_response(self.get_context_data(form=form))


class FamilyFinanceView(ApprovedUserRequiredMixin, DetailView):
    model = Family
    template_name = "members/family_finance.html"

    def get_queryset(self):
        base = Family.objects.prefetch_related("services__payments", "athlete_profiles__athlete__person")
        if self.request.user.is_staff or self.request.user.is_superuser:
            return base
        return base.filter(id__in=self.request.user.get_accessible_family_ids())

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        family = self.object
        services = family.services.select_related("profile__athlete__person").prefetch_related("payments")
        service_rows = []
        for service in services:
            paid = sum((p.amount for p in service.payments.all()), Decimal("0.00"))
            net_amount = service.amount - service.discount_value
            service_rows.append({
                "service": service,
                "net_amount": net_amount,
                "paid_amount": paid,
                "balance": net_amount - paid,
            })

        context.update(
            service_rows=service_rows,
            service_form=FamilyServiceForm(family=family),
            payment_form=FamilyPaymentForm(family=family),
            payments=payments,
        )
        return context

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()

        action = request.POST.get("action")
        if action == "create_service":
            form = FamilyServiceForm(request.POST, family=self.object)
            if form.is_valid():
                service = form.save(commit=False)
                service.family = self.object
                service.save()
                messages.success(request, "Услуга добавлена")
                return redirect("members:family_finance", pk=self.object.pk)
            payment_form = FamilyPaymentForm(family=self.object)
            service_form = form
        elif action == "create_payment":
            form = FamilyPaymentForm(request.POST, family=self.object)
            if form.is_valid():
                form.save()
                messages.success(request, "Платёж добавлен")
                return redirect("members:family_finance", pk=self.object.pk)
            service_form = FamilyServiceForm(family=self.object)
            payment_form = form
        else:
            return redirect("members:family_finance", pk=self.object.pk)

        context = self.get_context_data()
        context["service_form"] = service_form
        context["payment_form"] = payment_form
        return self.render_to_response(context)
