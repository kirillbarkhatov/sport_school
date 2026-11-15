import json

from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from users.constants import ADMIN_GROUP_NAME, MANAGER_GROUP_NAME
from users.mixins import ApprovedUserRequiredMixin

from . import services
from .core.parsing import detect_columns, parse_protocol
from .core.values import InvalidResult, validate_manual
from .forms import ColumnMappingsForm, EventForm, ManualResultForm
from .linking import link_event_entries
from .models import ResultEntry, ResultsEvent
from .sources import SourceError, build_source
from .state import build_state
from .tasks import enqueue_publication
from .telegram import public_url


def can_manage_results(user) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return user.groups.filter(name__in={ADMIN_GROUP_NAME, MANAGER_GROUP_NAME}).exists()


class ResultsManagerMixin(ApprovedUserRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not can_manage_results(request.user):
            return HttpResponseForbidden("Недостаточно прав.")
        return super().dispatch(request, *args, **kwargs)


class EventListView(ResultsManagerMixin, ListView):
    model = ResultsEvent
    template_name = "results/event_list.html"
    context_object_name = "events"
    paginate_by = 30

    def get_queryset(self):
        return ResultsEvent.objects.select_related("competition").order_by("-created_at")


class EventCreateView(ResultsManagerMixin, CreateView):
    model = ResultsEvent
    form_class = EventForm
    template_name = "results/event_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("results:event_detail", kwargs={"pk": self.object.pk})


class EventUpdateView(ResultsManagerMixin, UpdateView):
    model = ResultsEvent
    form_class = EventForm
    template_name = "results/event_form.html"

    def form_valid(self, form):
        if set(form.changed_data) & set(ResultsEvent.SOURCE_FIELDS):
            form.instance.bump_source_revision()
            response = super().form_valid(form)
            services.log_message(self.object.pk, "Источник протокола изменён", self.request.user)
            if self.object.status == ResultsEvent.Status.RUNNING:
                messages.info(self.request, "Источник изменён: воркер переключится на него при следующем опросе.")
            return response
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("results:event_detail", kwargs={"pk": self.object.pk})


class EventDetailView(ResultsManagerMixin, View):
    """Пульт трансляции: статус, источник, предпросмотр разбора, разметка колонок, участники, журнал."""

    template_name = "results/event_detail.html"
    actions = (
        "start",
        "pause",
        "finish",
        "reopen",
        "sync",
        "preview",
        "link",
        "mappings",
        "publish",
        "add_entry",
        "add_bulk",
    )

    def get(self, request, pk, preview=None, mappings_form=None):
        event = get_object_or_404(ResultsEvent.objects.select_related("competition", "telegram_channel"), pk=pk)
        entries = event.entries.filter(removed=False).select_related("group", "athlete__person")
        context = {
            "event": event,
            "state": build_state(event),
            "entries": entries.order_by("group__order_index", "sheet_row"),
            "log": event.log.order_by("-seq")[:50],
            "preview": preview,
            "mappings_form": mappings_form
            or ColumnMappingsForm(
                initial={
                    "mappings": json.dumps(event.column_mappings, ensure_ascii=False, indent=2)
                    if event.column_mappings
                    else ""
                }
            ),
            "public_url": public_url(event),
            "telegram": getattr(event, "telegram", None),
            "linked_count": entries.exclude(athlete=None).count(),
            "entries_count": entries.count(),
            "roster_groups": event.groups.filter(removed=False).order_by("order_index"),
        }
        return render(request, self.template_name, context)

    def post(self, request, pk):
        event = get_object_or_404(ResultsEvent, pk=pk)
        action = request.POST.get("action")
        if action not in self.actions:
            raise Http404
        return getattr(self, f"_{action}")(request, event)

    def _back(self, event):
        return redirect("results:event_detail", pk=event.pk)

    def _start(self, request, event):
        if event.source_kind == ResultsEvent.SourceKind.MANUAL and not event.entries.filter(removed=False).exists():
            messages.error(
                request, "Ручной эфир без участников запустить нельзя: добавьте группу и участников в «Состав»."
            )
            return self._back(event)
        services.set_status(event.pk, ResultsEvent.Status.RUNNING, request.user)
        messages.success(request, "Трансляция запущена: воркер начнёт опрос источника в течение нескольких секунд.")
        return self._back(event)

    def _pause(self, request, event):
        services.set_status(event.pk, ResultsEvent.Status.PAUSED, request.user)
        messages.info(request, "Трансляция на паузе.")
        return self._back(event)

    def _finish(self, request, event):
        services.set_status(event.pk, ResultsEvent.Status.FINISHED, request.user)
        messages.info(request, "Трансляция завершена.")
        return self._back(event)

    def _reopen(self, request, event):
        services.set_status(event.pk, ResultsEvent.Status.DRAFT, request.user)
        return self._back(event)

    def _fetch(self, request, event):
        try:
            source = build_source(event)
            if source is None:
                messages.info(request, "Ручной источник: данные вводятся на этой странице.")
                return None
            return source.fetch()
        except SourceError as exc:
            messages.error(request, f"Источник недоступен: {exc}")
            return None

    def _sync(self, request, event):
        revision, fetched_at = event.source_revision, timezone.now()
        sheets = self._fetch(request, event)
        if sheets is not None:
            result = services.ingest_snapshot(event.pk, sheets, source_revision=revision, fetched_at=fetched_at)
            if result.stale:
                messages.warning(request, "Источник изменили во время чтения — данные не применены, повторите.")
                return self._back(event)
            messages.success(request, f"Источник прочитан, изменений: {len(result.changes)}.")
            if result.changed or result.focus_changed:
                enqueue_publication(event.pk)
        return self._back(event)

    def _preview(self, request, event):
        sheets = self._fetch(request, event)
        if sheets is None:
            return self._back(event)
        protocol = parse_protocol(sheets, services.column_mappings(event))
        detected = {name: cmap.to_dict() for name, rows in sheets.items() if rows and (cmap := detect_columns(rows))}
        preview = {
            "protocol": protocol,
            "sheets": [(name, len(rows)) for name, rows in sheets.items()],
            "detected_json": json.dumps(detected, ensure_ascii=False, indent=2),
        }
        return self.get(request, event.pk, preview=preview)

    def _mappings(self, request, event):
        form = ColumnMappingsForm(request.POST)
        if not form.is_valid():
            return self.get(request, event.pk, mappings_form=form)
        event.column_mappings = form.cleaned_data["mappings"]
        event.bump_source_revision()
        event.save(update_fields=["column_mappings", "source_revision", "updated_at"])
        messages.success(request, "Разметка колонок сохранена. Она применится при следующем изменении источника.")
        return self._back(event)

    def _link(self, request, event):
        report = link_event_entries(event)
        messages.success(
            request,
            f"Связано: {report.linked}, неоднозначно: {report.ambiguous}, не найдено: {report.not_found}, "
            f"ручных связей сохранено: {report.kept_manual}.",
        )
        return self._back(event)

    def _add_entry(self, request, event):
        data = request.POST
        try:
            number = int(data.get("start_number") or 0)
            year = int(data["birth_year"]) if data.get("birth_year") else None
        except ValueError:
            messages.error(request, "Стартовый номер и год рождения — числа.")
            return self._back(event)
        row = {
            "group_name": (data.get("group_name") or "").strip(),
            "start_number": number,
            "full_name": " ".join((data.get("full_name") or "").split()),
            "club": (data.get("club") or "").strip(),
            "birth_year": year,
            "runs_count": 1 if data.get("runs_count") == "1" else 2,
        }
        if not row["group_name"]:
            messages.error(request, "Укажите группу.")
            return self._back(event)
        try:
            services.add_manual_entries(event.pk, [row], request.user)
        except services.RosterError as exc:
            messages.error(request, str(exc))
            return self._back(event)
        messages.success(request, f"Добавлен: {row['start_number']} {row['full_name']} ({row['group_name']})")
        return self._back(event)

    def _add_bulk(self, request, event):
        runs = 1 if request.POST.get("runs_count") == "1" else 2
        try:
            count = services.add_manual_entries(
                event.pk, services.parse_roster(request.POST.get("bulk", ""), runs), request.user
            )
        except services.RosterError as exc:
            messages.error(request, f"{exc}. Список не добавлен.")
            return self._back(event)
        messages.success(request, f"Добавлено участников: {count}")
        return self._back(event)

    def _publish(self, request, event):
        enqueue_publication(event.pk)
        messages.info(request, "Публикация в Telegram поставлена в очередь.")
        return self._back(event)


class PanelFragmentView(ResultsManagerMixin, View):
    """Автообновляемые части пульта: статус (?part=status) и журнал (?part=log)."""

    def get(self, request, pk):
        event = get_object_or_404(ResultsEvent.objects.select_related("telegram_channel"), pk=pk)
        if request.GET.get("part") == "log":
            response = render(request, "results/_panel_log.html", {"log": event.log.order_by("-seq")[:50]})
        else:
            entries = event.entries.filter(removed=False)
            response = render(
                request,
                "results/_panel_status.html",
                {
                    "event": event,
                    "state": build_state(event),
                    "entries_count": entries.count(),
                    "linked_count": entries.exclude(athlete=None).count(),
                },
            )
        response["Cache-Control"] = "no-store"
        return response


class EntryResultView(ResultsManagerMixin, View):
    """Ручная правка времени участника (или снятие правки)."""

    def post(self, request, pk, entry_id):
        entry = get_object_or_404(ResultEntry, pk=entry_id, event_id=pk)
        if request.POST.get("remove"):
            try:
                services.remove_manual_entry(entry.pk, request.user)
            except services.RosterError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, f"Убран: {entry.start_number} {entry.full_name}")
            return redirect("results:event_detail", pk=pk)
        form = ManualResultForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Некорректные данные")
            return redirect("results:event_detail", pk=pk)
        anchor = f"{reverse('results:event_detail', kwargs={'pk': pk})}#entry-{entry.pk}"
        if form.cleaned_data["reset"]:
            for run in (1, 2):
                if getattr(entry, f"run{run}_manual") is not None:
                    services.set_manual_result(entry.pk, run, None, request.user)
            enqueue_publication(pk)
            messages.success(request, f"Правка снята, значения из протокола: {entry.start_number} {entry.full_name}")
            return redirect(anchor)
        # Сначала проверяем оба значения, и только потом сохраняем — чтобы ошибка не оставила полуправку.
        runs = (1, 2) if entry.runs_count > 1 else (1,)
        planned = {}
        for run in runs:
            raw = form.cleaned_data[f"run{run}"].strip()
            if raw == entry.effective_raw(run):
                continue
            try:
                planned[run] = validate_manual(raw)
            except InvalidResult as exc:
                messages.error(
                    request, f"{entry.start_number} {entry.full_name}, {run} заезд: {exc} Изменения не сохранены."
                )
                return redirect(anchor)
        for run, value in planned.items():
            if value != entry.effective_raw(run):
                services.set_manual_result(entry.pk, run, value, request.user)
        if planned:
            enqueue_publication(pk)
            messages.success(request, f"Сохранено: {entry.start_number} {entry.full_name}")
        return redirect(anchor)


# --- Live-страницы --------------------------------------------------------------------------------


def _live_context(event: ResultsEvent, fragment_url: str, *, public: bool) -> dict:
    return {"event": event, "state": build_state(event), "fragment_url": fragment_url, "public": public}


def _fragment(request, event: ResultsEvent, fragment_url: str, public: bool) -> HttpResponse:
    """?v=<version> — если состояние не менялось, 204 без тела (клиент ничего не перерисовывает)."""
    if request.GET.get("v") == str(event.version):
        response = HttpResponse(status=204)
    else:
        response = render(request, "results/_live_body.html", _live_context(event, fragment_url, public=public))
    response["X-Results-Version"] = str(event.version)
    response["Cache-Control"] = "no-store"
    return response


class LiveView(ResultsManagerMixin, View):
    def get(self, request, pk):
        event = get_object_or_404(ResultsEvent, pk=pk)
        url = reverse("results:live_fragment", kwargs={"pk": pk})
        return render(request, "results/live.html", _live_context(event, url, public=False))


class LiveFragmentView(ResultsManagerMixin, View):
    def get(self, request, pk):
        event = get_object_or_404(ResultsEvent, pk=pk)
        return _fragment(request, event, reverse("results:live_fragment", kwargs={"pk": pk}), public=False)


def _public_available(event: ResultsEvent) -> bool:
    return event.public_enabled and event.status != ResultsEvent.Status.DRAFT


def _unavailable(request, status: int) -> HttpResponse:
    response = render(request, "results/_unavailable.html", status=status)
    response["Cache-Control"] = "no-store"
    return response


class PublicLiveView(View):
    def get(self, request, token):
        event = get_object_or_404(ResultsEvent, public_token=token)
        url = reverse("results:public_fragment", kwargs={"token": token})
        if not _public_available(event):
            # 410: страница остаётся живой и опрашивает фрагмент — если доступ вернут, результаты появятся сами
            response = render(
                request,
                "results/public_live.html",
                {"event": event, "fragment_url": url, "unavailable": True},
                status=410,
            )
            response["Cache-Control"] = "no-store"
            return response
        return render(request, "results/public_live.html", _live_context(event, url, public=True))


class PublicFragmentView(View):
    def get(self, request, token):
        event = ResultsEvent.objects.filter(public_token=token).first()
        if event is None:
            return _unavailable(request, 404)
        if not _public_available(event):
            return _unavailable(request, 410)
        return _fragment(request, event, reverse("results:public_fragment", kwargs={"token": token}), public=True)
