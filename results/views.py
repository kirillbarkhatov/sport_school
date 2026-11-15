import json

from django.contrib import messages
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, ListView, UpdateView

from users.constants import ADMIN_GROUP_NAME, MANAGER_GROUP_NAME
from users.mixins import ApprovedUserRequiredMixin

from . import services
from .core.parsing import detect_columns, parse_protocol
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

    def get_success_url(self):
        return reverse("results:event_detail", kwargs={"pk": self.object.pk})


class EventDetailView(ResultsManagerMixin, View):
    """Пульт трансляции: статус, источник, предпросмотр разбора, разметка колонок, участники, журнал."""

    template_name = "results/event_detail.html"
    actions = ("start", "pause", "finish", "reopen", "sync", "preview", "link", "mappings", "publish")

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
        sheets = self._fetch(request, event)
        if sheets is not None:
            result = services.ingest_snapshot(event.pk, sheets)
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
        event.save(update_fields=["column_mappings", "updated_at"])
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

    def _publish(self, request, event):
        enqueue_publication(event.pk)
        messages.info(request, "Публикация в Telegram поставлена в очередь.")
        return self._back(event)


class EntryResultView(ResultsManagerMixin, View):
    """Ручная правка времени участника (или снятие правки)."""

    def post(self, request, pk, entry_id):
        entry = get_object_or_404(ResultEntry, pk=entry_id, event_id=pk)
        form = ManualResultForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Некорректные данные")
            return redirect("results:event_detail", pk=pk)
        for run in (1, 2):
            if form.cleaned_data["reset"]:
                if getattr(entry, f"run{run}_manual") is not None:
                    services.set_manual_result(entry.pk, run, None, request.user)
                continue
            value = form.cleaned_data[f"run{run}"].strip()
            if value != entry.effective_raw(run):
                services.set_manual_result(entry.pk, run, value, request.user)
        enqueue_publication(pk)
        messages.success(request, f"Сохранено: {entry.start_number} {entry.full_name}")
        return redirect(f"{reverse('results:event_detail', kwargs={'pk': pk})}#entry-{entry.pk}")


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


def _public_event(token: str) -> ResultsEvent:
    event = get_object_or_404(ResultsEvent, public_token=token)
    if not event.public_enabled or event.status == ResultsEvent.Status.DRAFT:
        raise Http404
    return event


class PublicLiveView(View):
    def get(self, request, token):
        event = _public_event(token)
        url = reverse("results:public_fragment", kwargs={"token": token})
        return render(request, "results/public_live.html", _live_context(event, url, public=True))


class PublicFragmentView(View):
    def get(self, request, token):
        event = _public_event(token)
        return _fragment(request, event, reverse("results:public_fragment", kwargs={"token": token}), public=True)
