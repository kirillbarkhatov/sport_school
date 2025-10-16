from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView, UpdateView, DeleteView

from users.mixins import ApprovedUserRequiredMixin
from users.utils import get_person_queryset_for_user
from school.forms import (
    PersonForm,
    FamilyForm,
    FamilyMemberInlineFormSet,
    ExistingFamilyMemberFormSet,
    FamilyAthleteProfileFormSet,
    FamilyServiceForm,
    FamilyPaymentForm,
    AthleteContractForm,
)
from school.models import Person, Family, FamilyMember, Athlete, FamilyAthleteProfile, FamilyService, FamilyPayment
from school.models import DiscountType, ServiceType, AthleteContract
from school.services import (
    ensure_monthly_service_for_contract,
    get_month_range,
)

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
        # ensure profiles exist для семейных спортсменов
        for membership in family.members.select_related("person"):
            person = membership.person
            if hasattr(person, "athlete"):
                profile, _ = FamilyAthleteProfile.objects.get_or_create(
                    family=family,
                    athlete=person.athlete,
                    defaults={
                        "monthly_fee": family.base_monthly_fee,
                        "discount_type": family.discount_type,
                        "discount_value": family.discount_value,
                    },
                )
        profiles = (
            FamilyAthleteProfile.objects.filter(family=family)
            .select_related("athlete__person")
            .prefetch_related("services__payments", "contracts")
        )

        today = timezone.now().date()
        month_start, _, next_month = get_month_range(today)

        athlete_rows = []
        for profile in profiles:
            contracts = list(profile.contracts.all())
            active_contract = next((contract for contract in contracts if contract.status == "active"), None)
            if active_contract:
                ensure_monthly_service_for_contract(active_contract, today)

            target_contract = active_contract or (contracts[0] if contracts else None)

            month_services_qs = profile.services.filter(
                service_type=ServiceType.MONTHLY,
                created_at__date__gte=month_start,
                created_at__date__lt=next_month,
            )
            if target_contract:
                month_services_qs = month_services_qs.filter(contract=target_contract)

            month_services = list(month_services_qs)
            amount_due = sum((service.amount - service.discount_value for service in month_services if not service.is_closed), Decimal("0.00"))
            amount_paid = Decimal("0.00")
            for service in month_services:
                amount_paid += sum(
                    (
                        payment.amount
                        for payment in service.payments.filter(
                            paid_at__gte=month_start,
                            paid_at__lt=next_month,
                        )
                    ),
                    Decimal("0.00"),
                )
            balance = amount_due - amount_paid

            if target_contract:
                contract_status = target_contract.status
                contract_fee = target_contract.base_fee
                contract_discount = target_contract.discount_value
            else:
                contract_status = "missing"
                contract_fee = Decimal("0.00")
                contract_discount = Decimal("0.00")

            athlete_rows.append(
                {
                    "athlete": profile.athlete,
                    "profile": profile,
                    "contract": target_contract,
                    "contract_status": contract_status,
                    "contract_fee": contract_fee,
                    "contract_discount": contract_discount,
                    "amount_due": amount_due,
                    "amount_paid": amount_paid,
                    "balance": balance,
                    "contract_create_url": reverse("members:contract_create", args=[family.pk, profile.pk]),
                    "contract_edit_url": reverse("members:contract_update", args=[family.pk, target_contract.pk]) if active_contract else None,
                    "create_label": "Создать договор на следующий сезон" if active_contract else "Создать договор",
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
        payments = FamilyPayment.objects.filter(service__family=family).select_related("service")
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


class FamilyServiceUpdateView(ApprovedUserRequiredMixin, UpdateView):
    model = FamilyService
    form_class = FamilyServiceForm
    template_name = "members/family_service_form.html"
    pk_url_kwarg = "service_pk"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        self.family = get_object_or_404(Family, pk=self.kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return FamilyService.objects.filter(family=self.family).select_related("profile__athlete__person")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["family"] = self.family
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["name"].widget.attrs.pop("readonly", None)
        return form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["family"] = self.family
        return context

    def form_valid(self, form):
        self.object = form.save()
        messages.success(self.request, "Услуга обновлена")
        return redirect("members:family_finance", pk=self.family.pk)


class FamilyServiceDeleteView(ApprovedUserRequiredMixin, DeleteView):
    model = FamilyService
    pk_url_kwarg = "service_pk"
    template_name = "members/family_service_confirm_delete.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        self.family = get_object_or_404(Family, pk=self.kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return FamilyService.objects.filter(family=self.family)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["family"] = self.family
        return context

    def get_success_url(self):
        messages.success(self.request, "Счёт удалён")
        return reverse("members:family_finance", kwargs={"pk": self.family.pk})


class AthleteContractCreateView(ApprovedUserRequiredMixin, CreateView):
    form_class = AthleteContractForm
    template_name = "members/family_contract_create.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        self.family = get_object_or_404(Family, pk=self.kwargs["pk"])
        self.profile = get_object_or_404(FamilyAthleteProfile, pk=self.kwargs["profile_pk"], family=self.family)
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["profile"] = self.profile
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"family": self.family, "profile": self.profile})
        return context

    def form_valid(self, form):
        contract = form.save(commit=False)
        contract.profile = self.profile
        contract.save()
        ensure_monthly_service_for_contract(contract)
        messages.success(self.request, "Договор создан")
        return redirect("members:family_detail", pk=self.family.pk)


class AthleteContractUpdateView(ApprovedUserRequiredMixin, UpdateView):
    model = AthleteContract
    form_class = AthleteContractForm
    template_name = "members/family_contract_update.html"
    pk_url_kwarg = "contract_pk"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        self.family = get_object_or_404(Family, pk=self.kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return AthleteContract.objects.filter(profile__family=self.family).select_related("profile__athlete__person")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["profile"] = self.object.profile
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({"family": self.family, "profile": self.object.profile})
        return context

    def form_valid(self, form):
        contract = form.save()
        ensure_monthly_service_for_contract(contract)
        messages.success(self.request, "Договор обновлён")
        return redirect("members:family_detail", pk=self.family.pk)
