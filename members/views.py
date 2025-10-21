from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.db import transaction
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        can_manage = self.request.user.is_staff or self.request.user.is_superuser
        context["can_manage_people"] = can_manage
        if can_manage:
            context["families"] = list(Family.objects.order_by("family_name"))
            context["relation_choices"] = FamilyMember.FAMILY_RELATION
            context["person_form"] = PersonForm()
        return context


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
    success_url = reverse_lazy("members:members_list")

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()
        return super().dispatch(request, *args, **kwargs)

    def _is_ajax(self) -> bool:
        return self.request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"

    def form_invalid(self, form):
        response = super().form_invalid(form)
        if self._is_ajax():
            errors = {}
            for field, messages_list in form.errors.get_json_data().items():
                errors[field] = [message["message"] for message in messages_list]
            return JsonResponse({"success": False, "errors": errors}, status=400)
        return response

    def form_valid(self, form):
        request = self.request
        family_id = request.POST.get("family_id") or ""
        relation = (request.POST.get("relation") or "").strip()
        make_contact = request.POST.get("make_family_contact") in {"on", "true", "1"}

        selected_family = None
        membership_payload = None

        if family_id:
            try:
                selected_family = Family.objects.get(pk=family_id)
            except (Family.DoesNotExist, ValueError):
                form.add_error(None, "Выбранная семья не найдена.")
                return self.form_invalid(form)

            valid_relations = {value for value, _ in FamilyMember.FAMILY_RELATION}
            if relation not in valid_relations:
                form.add_error(None, "Укажите корректное родственное отношение.")
                return self.form_invalid(form)
        elif relation:
            form.add_error(None, "Для указанного родства выберите семью.")
            return self.form_invalid(form)

        if make_contact and not selected_family:
            form.add_error(None, "Чтобы сделать человека контактом, необходимо выбрать семью.")
            return self.form_invalid(form)

        with transaction.atomic():
            self.object = form.save()

            if selected_family:
                membership, _ = FamilyMember.objects.get_or_create(
                    family=selected_family,
                    person=self.object,
                    defaults={"relation": relation},
                )
                if membership.relation != relation:
                    membership.relation = relation
                    membership.save(update_fields=["relation"])
                if make_contact and selected_family.contact_person_id != self.object.pk:
                    selected_family.contact_person = self.object
                    selected_family.save(update_fields=["contact_person"])

                membership_payload = {
                    "family_id": selected_family.pk,
                    "family_name": selected_family.family_name or "Без названия",
                    "relation": membership.relation,
                    "relation_display": dict(FamilyMember.FAMILY_RELATION).get(
                        membership.relation, membership.relation
                    ),
                }

        messages.success(request, "Участник успешно добавлен.")

        if self._is_ajax():
            return JsonResponse(
                {
                    "success": True,
                    "person_id": self.object.pk,
                    "redirect_url": str(self.get_success_url()),
                    "membership": membership_payload,
                    "message": "Участник успешно добавлен.",
                },
                status=201,
            )

        return redirect(self.get_success_url())


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
    success_url = reverse_lazy("members:members_list")
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
        families = list(self.get_queryset())
        context["active_families"] = [f for f in families if f.status == Family.STATUS_ACTIVE]
        context["alumni_families"] = [f for f in families if f.status == Family.STATUS_ALUMNI]
        context["family_form"] = getattr(self, "family_form", FamilyForm())
        context["member_formset"] = getattr(
            self,
            "member_formset",
            FamilyMemberInlineFormSet(prefix="members"),
        )
        if self.request.user.is_staff or self.request.user.is_superuser:
            self._attach_candidate_people(families)
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
                        "monthly_fee": Decimal("0.00"),
                        "discount_type": DiscountType.NONE,
                        "discount_value": Decimal("0.00"),
                    },
                )

    def _attach_candidate_people(self, families: list[Family]) -> None:
        people = list(Person.objects.all().order_by("surname", "name"))
        for family in families:
            existing_ids = {membership.person_id for membership in family.members.all()}
            candidates = [person for person in people if person.id not in existing_ids]
            family_prefix = (family.family_name or "").strip().lower()[:3]
            candidates.sort(
                key=lambda person: (
                    0
                    if family_prefix
                    and (person.surname or "").strip().lower()[:3] == family_prefix
                    else 1,
                    person.surname.lower(),
                    person.name.lower(),
                )
            )
            family.candidate_people = candidates
            family.relation_choices = FamilyMember.FAMILY_RELATION


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
                        "monthly_fee": Decimal("0.00"),
                        "discount_type": DiscountType.NONE,
                        "discount_value": Decimal("0.00"),
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
        if self.request.user.is_staff or self.request.user.is_superuser:
            context["contact_people"] = Person.objects.order_by("surname", "name")
            context["status_choices"] = Family.STATUS_CHOICES
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


class PersonToggleAthleteView(ApprovedUserRequiredMixin, View):
    """Переключение статуса спортсмена для человека."""

    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()

        person = get_object_or_404(Person, pk=self.kwargs["pk"])
        redirect_url = request.POST.get("next") or reverse("members:members_list")
        is_ajax = request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"

        if person.is_athlete:
            person.athlete.delete()
            message = f"{person} исключён из списка спортсменов."
            if is_ajax:
                return JsonResponse(
                    {
                        "success": True,
                        "is_athlete": False,
                        "message": message,
                    }
                )
            messages.success(request, message)
            return redirect(redirect_url)

        level_value = Athlete.LEVEL_CHOICES[-1][0]
        athlete = Athlete.objects.create(person=person, level=level_value)

        for membership in person.familymember_set.select_related("family"):
            family = membership.family
            FamilyAthleteProfile.objects.get_or_create(
                family=family,
                athlete=athlete,
                defaults={
                    "monthly_fee": Decimal("0.00"),
                    "discount_type": DiscountType.NONE,
                    "discount_value": Decimal("0.00"),
                },
            )

        message = f"{person} добавлен в список спортсменов."
        if is_ajax:
            return JsonResponse(
                {
                    "success": True,
                    "is_athlete": True,
                    "message": message,
                }
            )
        messages.success(request, message)
        return redirect(redirect_url)


class PersonAssignFamilyView(ApprovedUserRequiredMixin, View):
    """Назначение человека в семью или удаление связи."""

    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()

        person = get_object_or_404(Person, pk=self.kwargs["pk"])
        family_id = request.POST.get("family_id") or ""
        relation = (request.POST.get("relation") or "").strip()
        redirect_url = request.POST.get("next") or reverse("members:members_list")
        is_ajax = request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"

        existing_family_ids = list(person.familymember_set.values_list("family_id", flat=True))

        if not family_id:
            FamilyMember.objects.filter(person=person).delete()
            if person.is_athlete and existing_family_ids:
                FamilyAthleteProfile.objects.filter(
                    family_id__in=existing_family_ids,
                    athlete=person.athlete,
                ).delete()
            messages.success(request, f"Человек {person} удалён из семьи.")
            if is_ajax:
                return JsonResponse(
                    {
                        "success": True,
                        "membership": None,
                        "message": f"Человек {person} удалён из семьи.",
                    }
                )
            return redirect(redirect_url)

        family = get_object_or_404(Family, pk=family_id)
        valid_relations = {value for value, _ in FamilyMember.FAMILY_RELATION}
        if not relation or relation not in valid_relations:
            error_message = "Укажите корректное родственное отношение."
            if is_ajax:
                return JsonResponse({"success": False, "errors": [error_message]}, status=400)
            messages.error(request, error_message)
            return redirect(redirect_url)

        FamilyMember.objects.filter(person=person).exclude(family=family).delete()
        membership, created = FamilyMember.objects.get_or_create(
            family=family,
            person=person,
            defaults={"relation": FamilyMember.FAMILY_RELATION[0][0]},
        )
        updated = False
        if not created and membership.relation != relation:
            membership.relation = relation
            membership.save(update_fields=["relation"])
            updated = True
        elif created and membership.relation != relation:
            membership.relation = relation
            membership.save(update_fields=["relation"])

        if person.is_athlete:
            FamilyAthleteProfile.objects.filter(
                family_id__in=[fid for fid in existing_family_ids if fid != family.pk],
                athlete=person.athlete,
            ).delete()
            FamilyAthleteProfile.objects.get_or_create(
                family=family,
                athlete=person.athlete,
                defaults={
                    "monthly_fee": Decimal("0.00"),
                    "discount_type": DiscountType.NONE,
                    "discount_value": Decimal("0.00"),
                },
            )

        relation_display = dict(FamilyMember.FAMILY_RELATION).get(membership.relation, membership.relation)
        action = "добавлен" if created else "обновлён" if updated else "подтверждён"
        action_message = f"{person} {action} в семье {family}."
        messages.success(request, action_message)
        if is_ajax:
            return JsonResponse(
                {
                    "success": True,
                    "membership": {
                        "family_id": family.pk,
                        "family_name": family.family_name or "Без названия",
                        "relation": membership.relation,
                        "relation_display": relation_display,
                    },
                    "message": action_message,
                }
            )
        return redirect(redirect_url)


class FamilyToggleStatusView(ApprovedUserRequiredMixin, View):
    """Переключение статуса семьи между действующей и бывшей."""

    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()

        family = get_object_or_404(Family, pk=self.kwargs["pk"])
        next_status = Family.STATUS_ALUMNI if family.status == Family.STATUS_ACTIVE else Family.STATUS_ACTIVE
        family.status = next_status
        family.save(update_fields=["status"])
        status_label = dict(Family.STATUS_CHOICES).get(next_status, next_status)
        messages.success(request, f"Статус семьи обновлён: {status_label.lower()}.")
        redirect_url = request.POST.get("next") or reverse("members:family_list")
        return redirect(redirect_url)


class FamilyInlineUpdateView(ApprovedUserRequiredMixin, View):
    """Обновление отдельных полей семьи без полной формы."""

    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()

        family = get_object_or_404(Family, pk=self.kwargs["pk"])
        field = (request.POST.get("field") or "").strip()
        value = (request.POST.get("value") or "").strip()

        try:
            if field == "family_name":
                family.family_name = value or None
                family.full_clean()
                family.save(update_fields=["family_name"])
                return JsonResponse(
                    {
                        "success": True,
                        "family_name": family.family_name or "Без названия",
                        "message": "Название семьи обновлено.",
                    }
                )

            if field == "contact_person":
                contact_person = None
                if value:
                    contact_person = get_object_or_404(Person, pk=value)
                family.contact_person = contact_person
                family.full_clean()
                family.save(update_fields=["contact_person"])
                return JsonResponse(
                    {
                        "success": True,
                        "contact_person": str(contact_person) if contact_person else "Не указано",
                        "contact_person_id": contact_person.pk if contact_person else None,
                        "message": "Контактное лицо обновлено.",
                    }
                )

            if field == "status":
                valid_status = {value for value, _ in Family.STATUS_CHOICES}
                if value not in valid_status:
                    return JsonResponse(
                        {"success": False, "errors": ["Некорректный статус."]},
                        status=400,
                    )
                family.status = value
                family.full_clean()
                family.save(update_fields=["status"])
                return JsonResponse(
                    {
                        "success": True,
                        "status": family.status,
                        "status_display": family.get_status_display(),
                        "message": "Статус семьи обновлён.",
                    }
                )
        except ValidationError as exc:
            error_list = []
            for messages_list in exc.message_dict.values():
                error_list.extend(messages_list)
            error_list = error_list or [exc.message]
            return JsonResponse({"success": False, "errors": error_list}, status=400)

        return JsonResponse(
            {"success": False, "errors": ["Неподдерживаемое поле для обновления."]},
            status=400,
        )


class FamilyAddMemberView(ApprovedUserRequiredMixin, View):
    """Добавление существующего человека в семью."""

    def post(self, request, *args, **kwargs):
        if not request.user.is_staff and not request.user.is_superuser:
            return self.handle_no_permission()

        family = get_object_or_404(Family, pk=self.kwargs["pk"])
        is_ajax = request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
        redirect_url = request.POST.get("next") or reverse("members:family_list")
        person_id = request.POST.get("person_id")
        relation = request.POST.get("relation")

        if not person_id or not relation:
            messages.error(request, "Выберите участника и укажите отношение.")
            if is_ajax:
                return JsonResponse(
                    {"success": False, "errors": ["Выберите участника и укажите отношение."]},
                    status=400,
                )
            return redirect(redirect_url)

        valid_relations = {value for value, _ in FamilyMember.FAMILY_RELATION}
        if relation not in valid_relations:
            messages.error(request, "Некорректный тип отношения.")
            if is_ajax:
                return JsonResponse(
                    {"success": False, "errors": ["Некорректный тип отношения."]},
                    status=400,
                )
            return redirect(redirect_url)

        person = get_object_or_404(Person, pk=person_id)
        membership, created = FamilyMember.objects.get_or_create(
            family=family,
            person=person,
            defaults={"relation": relation},
        )
        if not created and membership.relation != relation:
            membership.relation = relation
            membership.save(update_fields=["relation"])
            action_message = f"{person} обновлён в семье."
        elif created:
            action_message = f"{person} добавлен в семью."
        else:
            action_message = f"{person} уже состоит в семье."

        if person.is_athlete:
            FamilyAthleteProfile.objects.get_or_create(
                family=family,
                athlete=person.athlete,
                defaults={
                    "monthly_fee": Decimal("0.00"),
                    "discount_type": DiscountType.NONE,
                    "discount_value": Decimal("0.00"),
                },
            )

        relation_display = membership.get_relation_display()
        messages.success(request, action_message)
        if is_ajax:
            member_html = render_to_string(
                "members/includes/family_member_item.html",
                {"membership": membership},
                request=request,
            )
            return JsonResponse(
                {
                    "success": True,
                    "message": action_message,
                    "member": {
                        "id": membership.pk,
                        "person_id": person.pk,
                        "full_name": str(person),
                        "relation": membership.relation,
                        "relation_display": relation_display,
                        "html": member_html,
                        "created": created,
                    },
                }
            )
        return redirect(redirect_url)
