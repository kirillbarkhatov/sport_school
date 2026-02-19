import json
from datetime import date

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import JsonResponse, HttpResponse
from django.shortcuts import render, get_object_or_404, redirect
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView, ListView

from school.forms import (
    AthleteForm,
    AthleteCompactForm,
    PersonForm,
    PersonCompactForm,
    FamilyForm,
    FamilyMemberForm,
    CompetitionForm,
)
from school.models import Athlete, Family, FamilyMember, Group, Club, Competition, CompetitionEntry
from users.mixins import ApprovedUserRequiredMixin
from users.utils import (
    get_athlete_queryset_for_user,
    get_person_queryset_for_user,
    get_group_queryset_for_user,
    get_class_queryset_for_user,
)


# Create your views here.


class IndexView(ApprovedUserRequiredMixin, TemplateView):
    """Стартовая страница"""
    template_name = "school/index.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user

        person_qs = get_person_queryset_for_user(user)
        athlete_qs = get_athlete_queryset_for_user(user)
        group_qs = get_group_queryset_for_user(user)
        classes_qs = get_class_queryset_for_user(user)

        context.update(
            family_count=len(user.get_accessible_family_ids()) if not user.is_staff else Family.objects.count(),
            person_count=person_qs.count(),
            athlete_count=athlete_qs.count(),
            group_count=group_qs.count(),
            upcoming_classes=classes_qs.select_related("group").order_by("date")[:5],
        )
        return context


class AthleteListView(ApprovedUserRequiredMixin, ListView):
    model = Athlete

    def get_queryset(self):
        return (
            get_athlete_queryset_for_user(self.request.user)
            .select_related("person")
            .prefetch_related("groups_athletes")
            .order_by("person__surname", "person__name")
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["level_choices"] = Athlete.LEVEL_CHOICES
        context["groups"] = (
            get_group_queryset_for_user(user)
            .prefetch_related("athletes")
            .order_by("name")
        )
        return context


class AthleteSimpleListView(ApprovedUserRequiredMixin, ListView):
    """Новый упрощённый список спортсменов (3 колонки, без инлайн-редактирования)."""

    template_name = "school/athlete_simple_list.html"
    model = Athlete

    def get_queryset(self):
        queryset = (
            get_athlete_queryset_for_user(self.request.user)
            .select_related("person")
            .prefetch_related(
                Prefetch("groups_athletes", queryset=Group.objects.order_by("name"))
            )
        )
        club_id = self.request.GET.get("club")
        if club_id:
            queryset = queryset.filter(person__club_id=club_id)

        order = self.request.GET.get("order")
        if order == "dob":
            return queryset.order_by("person__date_of_birth", "person__surname", "person__name")
        if order == "group":
            # Сортируем по названию группы; distinct чтобы избежать дубликатов из-за M2M
            return queryset.order_by("groups_athletes__name", "person__surname", "person__name").distinct()
        return queryset.order_by("person__surname", "person__name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["order"] = self.request.GET.get("order") or ""
        context["clubs"] = Club.objects.order_by("name")
        context["selected_club"] = self.request.GET.get("club") or ""
        order = context["order"]
        grouped = []

        if order == "dob":
            buckets = {}
            for athlete in context["object_list"]:
                year = None
                if athlete.person and athlete.person.date_of_birth:
                    year = athlete.person.date_of_birth.year
                label = year
                buckets.setdefault(label, []).append(athlete)
            grouped = sorted(
                ((("Без даты" if k is None else str(k)), v) for k, v in buckets.items()),
                key=lambda x: (x[0] == "Без даты", x[0]),
            )
        elif order == "group":
            buckets = {}
            for athlete in context["object_list"]:
                main_group = athlete.groups_athletes.first()
                label = main_group.name if main_group else "Без группы"
                buckets.setdefault(label, []).append(athlete)
            grouped = sorted(buckets.items(), key=lambda x: (x[0] == "Без группы", x[0]))

        context["grouped"] = grouped
        return context


# def edit_athlete(request, athlete_id):
#     athlete = get_object_or_404(Athlete, id=athlete_id)
#     person = athlete.person  # Получаем связанные данные из модели Person
#
#     if request.method == "POST":
#         athlete_form = AthleteForm(request.POST, instance=athlete)
#         person_form = PersonForm(request.POST, instance=person)
#         if athlete_form.is_valid() and person_form.is_valid():
#             athlete_form.save()
#             person_form.save()
#     else:
#         athlete_form = AthleteForm(instance=athlete)
#         person_form = PersonForm(instance=person)
#
#     return render(request, 'athletes/edit_form.html', {
#         'athlete_form': athlete_form,
#         'person_form': person_form,
#         'athlete': athlete,
#     })


@login_required
def edit_athlete(request, athlete_id):
    athlete = get_object_or_404(Athlete, id=athlete_id)

    if not request.user.is_staff and not request.user.is_superuser:
        allowed = get_athlete_queryset_for_user(request.user)
        if not allowed.filter(id=athlete.id).exists():
            if request.user.is_approved:
                return redirect("school:athlete_list")
            return redirect("users:awaiting_approval")

    person = athlete.person

    # Ищем семьи, к которым относится этот спортсмен
    family_memberships = FamilyMember.objects.filter(person=person)
    families = [membership.family for membership in family_memberships]

    # В случае нескольких семей, будем использовать первую (в зависимости от логики приложения)
    family = families[0] if families else None
    family_members = FamilyMember.objects.filter(family=family) if family else []

    if request.method == "POST":
        athlete_form = AthleteForm(request.POST, instance=athlete)
        person_form = PersonForm(request.POST, instance=person)
        family_form = FamilyForm(request.POST, instance=family) if family else None
        family_member_forms = [
            FamilyMemberForm(request.POST, prefix=str(member.id), instance=member)
            for member in family_members
        ]

        if (
            athlete_form.is_valid() and
            person_form.is_valid() and
            (family_form is None or family_form.is_valid()) and
            all(form.is_valid() for form in family_member_forms)
        ):
            athlete_form.save()
            person_form.save()
            if family_form:
                family_form.save()
            for form in family_member_forms:
                form.save()

    else:
        athlete_form = AthleteForm(instance=athlete)
        person_form = PersonForm(instance=person)
        family_form = FamilyForm(instance=family) if family else None
        family_member_forms = [FamilyMemberForm(prefix=str(member.id), instance=member) for member in family_members]

    return render(
        request,
        "athletes/edit_form.html",
        {
            "athlete_form": athlete_form,
            "person_form": person_form,
            "family_form": family_form,
            "family_member_forms": family_member_forms,
            "athlete": athlete,
        },
    )


class AthleteCompactEditView(ApprovedUserRequiredMixin, View):
    """Мобильный компактный экран редактирования спортсмена."""

    template_name = "athletes/edit_compact.html"

    def get_athlete(self, request, pk):
        athlete = get_object_or_404(Athlete, pk=pk)
        if request.user.is_staff or request.user.is_superuser:
            return athlete
        allowed = get_athlete_queryset_for_user(request.user)
        if allowed.filter(id=athlete.id).exists():
            return athlete
        if request.user.is_approved:
            return None
        return None

    def get(self, request, pk):
        athlete = self.get_athlete(request, pk)
        if athlete is None:
            return redirect("school:athlete_list")
        person = athlete.person
        group_qs = get_group_queryset_for_user(request.user)
        person_form = PersonCompactForm(instance=person)
        athlete_form = AthleteCompactForm(instance=athlete, group_qs=group_qs)
        return render(
            request,
            self.template_name,
            {
                "athlete": athlete,
                "person_form": person_form,
                "athlete_form": athlete_form,
                "saved": False,
            },
        )

    def post(self, request, pk):
        athlete = self.get_athlete(request, pk)
        if athlete is None:
            return redirect("school:athlete_list")
        person = athlete.person
        group_qs = get_group_queryset_for_user(request.user)
        person_form = PersonCompactForm(request.POST, request.FILES, instance=person)
        athlete_form = AthleteCompactForm(request.POST, instance=athlete, group_qs=group_qs)

        if person_form.is_valid() and athlete_form.is_valid():
            person_form.save()
            athlete_form.save()
            saved = True
        else:
            saved = False

        return render(
            request,
            self.template_name,
            {
                "athlete": athlete,
                "person_form": person_form,
                "athlete_form": athlete_form,
                "saved": saved,
            },
        )


class AthleteInlineUpdateView(ApprovedUserRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request, pk):
        athlete = (
            get_athlete_queryset_for_user(request.user)
            .select_related("person")
            .prefetch_related("groups_athletes")
            .filter(pk=pk)
            .first()
        )
        if not athlete:
            return JsonResponse({"error": "not_found"}, status=404)

        try:
            payload = json.loads(request.body or "{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "invalid_payload"}, status=400)

        person = athlete.person
        updated_fields: list[str] = []
        response_payload: dict[str, object] = {}

        birth_year = payload.get("birth_year")
        if birth_year is not None and person:
            try:
                year = int(birth_year)
            except (TypeError, ValueError):
                return JsonResponse({"error": "invalid_birth_year"}, status=400)
            current_year = timezone.now().year
            if year < 1900 or year > current_year:
                return JsonResponse({"error": "invalid_birth_year"}, status=400)
            if person.date_of_birth:
                month = person.date_of_birth.month
                day = person.date_of_birth.day
                try:
                    new_date = person.date_of_birth.replace(year=year)
                except ValueError:
                    if month == 2 and day == 29:
                        new_date = person.date_of_birth.replace(year=year, month=2, day=28)
                    else:
                        return JsonResponse({"error": "invalid_birth_year"}, status=400)
            else:
                new_date = date(year, 1, 1)
            if person.date_of_birth != new_date:
                person.date_of_birth = new_date
                person.save(update_fields=["date_of_birth"])
            response_payload["birth_year"] = year
        elif person and person.date_of_birth:
            response_payload["birth_year"] = person.date_of_birth.year
        else:
            response_payload["birth_year"] = None

        level = payload.get("level")
        if level is not None:
            valid_levels = {choice for choice, _ in Athlete.LEVEL_CHOICES}
            if level not in valid_levels:
                return JsonResponse({"error": "invalid_level"}, status=400)
            if athlete.level != level:
                athlete.level = level
                updated_fields.append("level")

        text_fields = {
            "rank": "rank",
            "medical_certificate": "medical_certificate",
            "comment": "comment",
        }
        for key, field in text_fields.items():
            if key in payload:
                value = payload.get(key) or ""
                normalized = value.strip()
                if getattr(athlete, field) != normalized:
                    setattr(athlete, field, normalized)
                    updated_fields.append(field)

        group_ids = payload.get("group_ids")
        if group_ids is not None:
            try:
                group_ids = [int(group_id) for group_id in group_ids]
            except (TypeError, ValueError):
                return JsonResponse({"error": "invalid_group_ids"}, status=400)
            allowed_group_ids = set(
                get_group_queryset_for_user(request.user).values_list("id", flat=True)
            )
            cleaned_group_ids = [gid for gid in group_ids if gid in allowed_group_ids]
            athlete.groups_athletes.set(cleaned_group_ids)

        if updated_fields:
            athlete.save(update_fields=updated_fields)

        refreshed_groups = list(
            Group.objects.filter(
                id__in=athlete.groups_athletes.values_list("id", flat=True)
            )
            .order_by("name")
            .values("id", "name")
        )

        response_payload.update(
            {
                "level": athlete.level,
                "rank": athlete.rank or "",
                "medical_certificate": athlete.medical_certificate or "",
                "comment": athlete.comment or "",
                "groups": refreshed_groups,
            }
        )
        return JsonResponse({"success": True, "athlete": response_payload})


class CompetitionListView(ApprovedUserRequiredMixin, ListView):
    model = Competition
    template_name = "competitions/competition_list.html"

    def get_queryset(self):
        return Competition.objects.prefetch_related("entries__athlete__person").order_by("-start_date", "-date", "name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["competition_form"] = CompetitionForm()
        return ctx


def _athlete_filter_queryset(user, club_id=None, year_from=None, year_to=None, order="surname"):
    qs = (
        get_athlete_queryset_for_user(user)
        .select_related("person__club")
        .prefetch_related(Prefetch("groups_athletes", queryset=Group.objects.order_by("name")))
    )
    if club_id:
        qs = qs.filter(person__club_id=club_id)
    if year_from:
        qs = qs.filter(person__date_of_birth__year__gte=year_from)
    if year_to:
        qs = qs.filter(person__date_of_birth__year__lte=year_to)
    if order == "dob":
        qs = qs.order_by("person__date_of_birth", "person__surname", "person__name")
    else:
        qs = qs.order_by("person__surname", "person__name")
    return qs


class CompetitionCreateUpdateView(ApprovedUserRequiredMixin, View):
    template_name = "competitions/competition_form.html"

    def get_object(self, pk):
        if pk is None:
            return None
        return get_object_or_404(Competition, pk=pk)

    def get(self, request, pk=None):
        competition = self.get_object(pk)
        form = CompetitionForm(instance=competition)
        club_id = request.GET.get("club") or ""
        year_from = request.GET.get("year_from") or ""
        year_to = request.GET.get("year_to") or ""
        order = request.GET.get("order") or "surname"
        athletes = _athlete_filter_queryset(
            request.user,
            club_id=None,
            year_from=None,
            year_to=None,
            order="surname",
        )
        selected_ids = set()
        if competition:
            selected_ids = set(competition.entries.values_list("athlete_id", flat=True))
        context = {
            "form": form,
            "competition": competition,
            "athletes": athletes,
            "clubs": Club.objects.order_by("name"),
            "selected_club": club_id,
            "year_from": year_from,
            "year_to": year_to,
            "order": order,
            "selected_ids": selected_ids,
        }
        return render(request, self.template_name, context)

    def post(self, request, pk=None):
        competition = self.get_object(pk)
        form = CompetitionForm(request.POST, instance=competition)
        save_competition = "save_competition" in request.POST
        save_athletes = "athletes_submit" in request.POST
        selected_ids = set(request.POST.getlist("athletes"))

        # Если просто сохраняем участников и соревнование уже существует — не требуем валидности формы
        if save_athletes and competition:
            selected_ids_int = [int(aid) for aid in selected_ids]
            existing = set(competition.entries.values_list("athlete_id", flat=True))
            competition.entries.exclude(athlete_id__in=selected_ids_int).delete()
            to_add = [aid for aid in selected_ids_int if aid not in existing]
            CompetitionEntry.objects.bulk_create(
                [CompetitionEntry(competition=competition, athlete_id=aid) for aid in to_add]
            )
            return redirect("school:competition_edit", pk=competition.pk)

        if form.is_valid():
            competition = form.save(commit=False)
            if competition.start_date is None and competition.date:
                competition.start_date = competition.date
            if competition.start_date is None and form.cleaned_data.get("start_date"):
                competition.start_date = form.cleaned_data["start_date"]
            competition.save()
            if save_athletes:
                selected_ids_int = [int(aid) for aid in selected_ids]
                existing = set(competition.entries.values_list("athlete_id", flat=True))
                competition.entries.exclude(athlete_id__in=selected_ids_int).delete()
                to_add = [aid for aid in selected_ids_int if aid not in existing]
                CompetitionEntry.objects.bulk_create(
                    [CompetitionEntry(competition=competition, athlete_id=aid) for aid in to_add]
                )
            if save_athletes or save_competition:
                return redirect("school:competition_edit", pk=competition.pk)

        # invalid form or no save flag: re-render with all athletes
        athletes = _athlete_filter_queryset(
            request.user,
            club_id=None,
            year_from=None,
            year_to=None,
            order="surname",
        )
        context = {
            "form": form,
            "competition": competition,
            "athletes": athletes,
            "clubs": Club.objects.order_by("name"),
            "selected_club": "",
            "year_from": "",
            "year_to": "",
            "order": "surname",
            "selected_ids": selected_ids,
        }
        return render(request, self.template_name, context)


def _excel_date_from_serial(serial):
    from datetime import datetime, timedelta
    try:
        base = datetime(1899, 12, 30)
        return base + timedelta(days=int(serial))
    except Exception:
        return None


def competition_export(request, pk):
    competition = get_object_or_404(Competition, pk=pk)
    entries = competition.entries.select_related("athlete__person", "athlete__person__club").order_by(
        "athlete__person__surname", "athlete__person__name"
    )
    rows = []
    for idx, entry in enumerate(entries, start=1):
        person = entry.athlete.person
        dob = person.date_of_birth.strftime("%d.%m.%Y") if person.date_of_birth else ""
        year = person.date_of_birth.year if person.date_of_birth else ""
        rows.append(
            [
                str(idx),
                " ".join(filter(None, [person.surname, person.name, person.middlename])),
                dob,
                entry.athlete.rank or "",
                "Всеволожский",  # муниципальный район — по примеру файла
                person.club.name if person.club else "",
            ]
        )

    # XML Spreadsheet with styles
    from django.utils.encoding import force_str

    def cell(value, style=None):
        style_attr = f" ss:StyleID='{style}'" if style else ""
        return f"<Cell{style_attr}><Data ss:Type='String'>{force_str(value)}</Data></Cell>"

    header = f'ЗАЯВКА НА УЧАСТИЕ СПОРТСМЕНОВ В СОРЕВНОВАНИЯХ "{competition.name}" ПО ГОРНОЛЫЖНОМУ СПОРТУ'
    if competition.location:
        subloc = f'НА {competition.location.upper()}'
    else:
        subloc = ""
    date_part = ""
    MONTHS_RU = {
        1: "января",
        2: "февраля",
        3: "марта",
        4: "апреля",
        5: "мая",
        6: "июня",
        7: "июля",
        8: "августа",
        9: "сентября",
        10: "октября",
        11: "ноября",
        12: "декабря",
    }

    def fmt_ru(d):
        return f"{d.day} {MONTHS_RU.get(d.month, '')} {d.year}"

    if competition.start_date and competition.end_date and competition.start_date != competition.end_date:
        date_part = f"{fmt_ru(competition.start_date)} - {fmt_ru(competition.end_date)}"
    elif competition.start_date:
        date_part = fmt_ru(competition.start_date)
    elif competition.date:
        date_part = fmt_ru(competition.date)
    subtitle = " ".join(filter(None, [subloc, date_part, "от команды Всеволожского района"])).strip()

    table_rows = []
    # Title rows
    table_rows.append(f"<Row><Cell ss:MergeAcross='5' ss:StyleID='sTitle'><Data ss:Type='String'>{force_str(header)}</Data></Cell></Row>")
    table_rows.append(f"<Row><Cell ss:MergeAcross='5' ss:StyleID='sSubtitle'><Data ss:Type='String'>{force_str(subtitle)}</Data></Cell></Row>")
    # Header row
    headers = ["№ п/п", "Фамилия, Имя", "Дата рождения", "Спортивный разряд", "Муниципальный район", "ФСО/Отделение"]
    table_rows.append("<Row>" + "".join(cell(h, "sHead") for h in headers) + "</Row>")
    # Data rows
    for r in rows:
        table_rows.append("<Row>" + "".join(cell(val, "sCell") for val in r) + "</Row>")

    styles = """
    <Styles>
      <Style ss:ID="sTitle">
        <Font ss:Bold="1" ss:Size="12"/>
        <Alignment ss:Horizontal="Center"/>
      </Style>
      <Style ss:ID="sSubtitle">
        <Font ss:Bold="1" ss:Size="11"/>
        <Alignment ss:Horizontal="Center"/>
      </Style>
      <Style ss:ID="sHead">
        <Font ss:Bold="1"/>
        <Alignment ss:Horizontal="Center"/>
        <Borders>
          <Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1"/>
          <Border ss:Position="Top" ss:LineStyle="Continuous" ss:Weight="1"/>
          <Border ss:Position="Left" ss:LineStyle="Continuous" ss:Weight="1"/>
          <Border ss:Position="Right" ss:LineStyle="Continuous" ss:Weight="1"/>
        </Borders>
      </Style>
      <Style ss:ID="sCell">
        <Borders>
          <Border ss:Position="Bottom" ss:LineStyle="Continuous" ss:Weight="1"/>
          <Border ss:Position="Top" ss:LineStyle="Continuous" ss:Weight="1"/>
          <Border ss:Position="Left" ss:LineStyle="Continuous" ss:Weight="1"/>
          <Border ss:Position="Right" ss:LineStyle="Continuous" ss:Weight="1"/>
        </Borders>
      </Style>
    </Styles>
    """

    columns = (
        "<Column ss:AutoFitWidth='0' ss:Width='25'/>"
        "<Column ss:AutoFitWidth='0' ss:Width='220'/>"
        "<Column ss:AutoFitWidth='0' ss:Width='80'/>"
        "<Column ss:AutoFitWidth='0' ss:Width='80'/>"
        "<Column ss:AutoFitWidth='0' ss:Width='120'/>"
        "<Column ss:AutoFitWidth='0' ss:Width='120'/>"
    )

    xml_content = (
        "<?xml version='1.0'?>"
        "<?mso-application progid='Excel.Sheet'?>"
        "<Workbook xmlns='urn:schemas-microsoft-com:office:spreadsheet' "
        "xmlns:ss='urn:schemas-microsoft-com:office:spreadsheet'>"
        f"{styles}"
        "<Worksheet ss:Name='Общая заявка'><Table>"
        f"{columns}"
        + "".join(table_rows) +
        "</Table></Worksheet></Workbook>"
    )
    resp = HttpResponse(xml_content, content_type="application/vnd.ms-excel")
    resp["Content-Disposition"] = f'attachment; filename=\"zayavka_{competition.pk}.xls\"'
    return resp
