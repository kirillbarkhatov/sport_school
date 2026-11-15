"""Интеграционные тесты results: снимки → БД → состояние, ручные правки, воркер, страницы, Telegram, replay."""

import copy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group as AuthGroup
from django.urls import reverse
from django.utils import timezone

from bot.models import TelegramChat
from results import services
from results.core import engine
from results.linking import link_event_entries
from results.models import EventLog, RawSnapshot, ResultEntry, ResultsEvent, TelegramPublication
from results.replay import replay
from results.state import build_state
from results.worker import EventRunner
from school.models import Athlete, Person
from users.constants import MANAGER_GROUP_NAME
from users.models import User

from .fixtures import protocols

pytestmark = pytest.mark.django_db

T0 = timezone.now().replace(microsecond=0)


@pytest.fixture
def event():
    return ResultsEvent.objects.create(title="Кубок", source_kind=ResultsEvent.SourceKind.MANUAL)


def _sheets():
    return copy.deepcopy(protocols.format_a_two_runs())


def _set(sheets, row, col, value):
    sheets["СЛГ"][row][col] = value
    return sheets


# Индексы строк в format_a_two_runs: 6 — группа девочек, 7..10 — участницы; 11 — мальчики, 12..15 — участники
EPSILON_ROW, ZETA_ROW, ETA_ROW, THETA_ROW = 12, 13, 14, 15
RUN1, RUN2 = 4, 5


def test_first_ingest_creates_groups_entries_and_snapshot(event):
    result = services.ingest_snapshot(event.pk, _sheets(), now=T0)
    event.refresh_from_db()
    assert result.changed
    assert event.groups.count() == 2 and event.entries.count() == 8
    assert event.snapshots.count() == 1
    assert event.protocol_title == "КУБОК ТЕСТОВОЙ ОБЛАСТИ" and event.protocol_date == "15.02.2026"
    assert event.version == 1
    # фокус инициализирован текущей группой (мальчики едут 1-й заезд, Тета ещё не стартовал)
    assert engine.Focus.from_dict(event.focus).current == engine.FocusTarget("СЛГ", "СЛГ|Мальчики 2014-2015 г.р.", 1)
    assert EventLog.objects.filter(event=event, message__startswith="Протокол загружен").exists()


def test_unchanged_snapshot_does_not_bump_version_or_store_duplicate(event):
    services.ingest_snapshot(event.pk, _sheets(), now=T0)
    result = services.ingest_snapshot(event.pk, _sheets(), now=T0 + timedelta(seconds=2))
    event.refresh_from_db()
    assert not result.changed and event.version == 1 and event.snapshots.count() == 1


def test_new_time_is_logged_sets_seen_at_and_moves_focus(event):
    services.ingest_snapshot(event.pk, _sheets(), now=T0)
    later = T0 + timedelta(seconds=30)
    result = services.ingest_snapshot(event.pk, _set(_sheets(), THETA_ROW, RUN1, "34.20"), now=later)
    event.refresh_from_db()
    assert [c.field for c in result.changes] == ["run1"]
    theta = event.entries.get(start_number=14)
    assert theta.run1_raw == "34.20" and theta.run1_seen_at == later
    assert theta.group.last_result_at == later
    assert EventLog.objects.filter(event=event, kind=EventLog.Kind.RESULT, message__contains="Тета Тимур").exists()
    assert event.version == 2


def test_timeout_finalization_bumps_version_without_new_snapshot(event):
    event.finalize_timeout_sec = 60
    event.save()
    sheets = _set(_sheets(), THETA_ROW, RUN1, "")
    services.ingest_snapshot(event.pk, sheets, now=T0)
    services.ingest_snapshot(
        event.pk, _set(copy.deepcopy(sheets), ETA_ROW, RUN1, "40.00"), now=T0 + timedelta(seconds=5)
    )
    event.refresh_from_db()
    version = event.version
    result = services.ingest_snapshot(
        event.pk, _set(copy.deepcopy(sheets), ETA_ROW, RUN1, "40.00"), now=T0 + timedelta(minutes=5)
    )
    event.refresh_from_db()
    assert result.changed and event.version == version + 1
    boys = next(g for g in build_state(event, now=T0 + timedelta(minutes=5)).groups if g.name.startswith("Мальчики"))
    theta = next(r for r in boys.rows if r.start_number == 14)
    assert theta.run1 == "DNS" and theta.auto_finalized


def test_removed_entry_is_marked_not_deleted(event):
    services.ingest_snapshot(event.pk, _sheets(), now=T0)
    sheets = _sheets()
    del sheets["СЛГ"][THETA_ROW]
    services.ingest_snapshot(event.pk, sheets, now=T0 + timedelta(seconds=5))
    theta = ResultEntry.objects.get(event=event, start_number=14)
    assert theta.removed
    assert all(e.start_number != 14 for e in services.load_protocol(event).entries)


def test_manual_result_overrides_source_and_can_be_reset(event):
    services.ingest_snapshot(event.pk, _sheets(), now=T0)
    theta = event.entries.get(start_number=14)
    services.set_manual_result(theta.pk, 1, "35,5")
    theta.refresh_from_db()
    assert theta.run1_manual == "35.50" and theta.effective_raw(1) == "35.50"  # ручной ввод нормализуется
    protocol = services.load_protocol(event)
    assert next(e for e in protocol.entries if e.start_number == 14).run1.ms == 35500
    # источник не затирает правку
    services.ingest_snapshot(event.pk, _set(_sheets(), THETA_ROW, RUN1, "36.00"), now=T0 + timedelta(seconds=5))
    theta.refresh_from_db()
    assert theta.run1_raw == "36.00" and theta.effective_raw(1) == "35.50"
    services.set_manual_result(theta.pk, 1, None)
    theta.refresh_from_db()
    assert theta.run1_manual is None and theta.effective_raw(1) == "36.00"
    assert EventLog.objects.filter(event=event, kind=EventLog.Kind.MANUAL).count() == 2


def test_status_lifecycle_logs(event):
    services.set_status(event.pk, ResultsEvent.Status.RUNNING)
    services.set_status(event.pk, ResultsEvent.Status.FINISHED)
    event.refresh_from_db()
    assert event.started_at and event.finished_at
    assert EventLog.objects.filter(event=event, kind=EventLog.Kind.LIFECYCLE).count() == 2


# --- state ----------------------------------------------------------------------------------------


def test_state_focus_group_finisher_and_highlight(event):
    event.highlight_club = "сш север"
    event.save()
    services.ingest_snapshot(event.pk, _sheets(), now=T0)
    services.ingest_snapshot(event.pk, _set(_sheets(), THETA_ROW, RUN1, "34.20"), now=T0 + timedelta(seconds=10))
    event.refresh_from_db()
    state = build_state(event)
    assert state.focus_group.name == "Мальчики 2014-2015 г.р."
    # Зета (33.80) с пометкой судьи идёт после чистых времён → Тета (34.20) второй
    assert state.finisher.full_name == "Тета Тимур" and state.finisher.place == 2
    zeta = next(r for r in state.focus_group.rows if r.full_name == "Зета Захар")
    assert zeta.highlight and zeta.note == "касание флага"
    girls = next(g for g in state.groups if g.name.startswith("Девочки"))
    assert girls.run1_rows  # группа во 2-м заезде — есть таблица после 1-го


# --- worker ---------------------------------------------------------------------------------------


class FakeSource:
    def __init__(self, sheets):
        self.sheets = sheets

    def fetch(self):
        return self.sheets


def test_worker_tick_ingests_and_enqueues_publication(event):
    event.source_kind = ResultsEvent.SourceKind.GOOGLE_SHEET
    event.status = ResultsEvent.Status.RUNNING
    event.save()
    runner = EventRunner(event.pk)
    with (
        patch("results.worker.build_source", return_value=FakeSource(_sheets())),
        patch("results.worker.enqueue_publication") as enqueue,
    ):
        pause = runner.tick()
    event.refresh_from_db()
    assert event.entries.count() == 8 and enqueue.called
    assert pause == event.poll_interval_sec  # были изменения → частый опрос


def test_worker_records_source_errors_with_backoff(event):
    from results.sources import SourceError

    event.status = ResultsEvent.Status.RUNNING
    event.source_kind = ResultsEvent.SourceKind.GOOGLE_SHEET
    event.save()

    class Broken:
        def fetch(self):
            raise SourceError("Google API: HTTP 500")

    runner = EventRunner(event.pk)
    with patch("results.worker.build_source", return_value=Broken()):
        assert runner.tick() == 5
        assert runner.tick() == 10
    event.refresh_from_db()
    assert "HTTP 500" in event.last_error
    assert EventLog.objects.filter(event=event, kind=EventLog.Kind.ERROR).exists()


def test_worker_stops_when_event_not_running(event):
    runner = EventRunner(event.pk)
    runner.tick()
    assert runner.stop_event.is_set()


def test_worker_finishes_completed_event_after_quiet_period(event):
    event.status = ResultsEvent.Status.RUNNING
    event.source_kind = ResultsEvent.SourceKind.UPLOAD
    event.finalize_timeout_sec = 0
    event.save()
    sheets = copy.deepcopy(protocols.format_a_lite_single_run())
    sheets["Малыши"][6][3] = "50.00"
    sheets["Малыши"][6][4] = "50.00"
    with (
        patch("results.worker.build_source", return_value=FakeSource(sheets)),
        patch("results.worker.enqueue_publication"),
    ):
        EventRunner(event.pk).tick()
    event.refresh_from_db()
    assert event.status == ResultsEvent.Status.FINISHED


# --- views ----------------------------------------------------------------------------------------


@pytest.fixture
def manager_client(client):
    user = User.objects.create(email="manager@example.com", is_approved=True)
    user.groups.add(AuthGroup.objects.get_or_create(name=MANAGER_GROUP_NAME)[0])
    client.force_login(user)
    return client


def test_manager_creates_event_and_controls_it(manager_client):
    response = manager_client.post(
        reverse("results:event_create"),
        {
            "title": "Первенство",
            "source_kind": "manual",
            "poll_interval_sec": 2,
            "idle_interval_sec": 15,
            "finalize_timeout_sec": 300,
            "finalize_max_missing": 2,
            "public_enabled": "on",
        },
    )
    event = ResultsEvent.objects.get(title="Первенство")
    assert response.status_code == 302 and response.url == reverse("results:event_detail", kwargs={"pk": event.pk})
    assert manager_client.get(response.url).status_code == 200
    manager_client.post(response.url, {"action": "start"})  # ручной эфир без участников не стартует
    event.refresh_from_db()
    assert event.status == ResultsEvent.Status.DRAFT
    manager_client.post(
        response.url,
        {
            "action": "add_entry",
            "group_name": "Девочки",
            "start_number": 1,
            "full_name": "Анна Альфа",
            "runs_count": 2,
        },
    )
    manager_client.post(response.url, {"action": "start"})
    event.refresh_from_db()
    assert event.status == ResultsEvent.Status.RUNNING


def test_form_requires_valid_google_link(manager_client):
    response = manager_client.post(
        reverse("results:event_create"),
        {
            "title": "X",
            "source_kind": "google_sheet",
            "source_ref": "not a link",
            "poll_interval_sec": 2,
            "idle_interval_sec": 15,
            "finalize_timeout_sec": 300,
            "finalize_max_missing": 2,
        },
    )
    assert response.status_code == 200
    assert "source_ref" in response.context["form"].errors


def test_mappings_form_validates_and_saves(manager_client, event):
    url = reverse("results:event_detail", kwargs={"pk": event.pk})
    bad = manager_client.post(url, {"action": "mappings", "mappings": '{"Лист1": {"club": 2}}'})
    assert bad.status_code == 200 and bad.context["mappings_form"].errors
    good = manager_client.post(
        url, {"action": "mappings", "mappings": '{"Лист1": {"start_number": 0, "full_name": 1, "club": 2, "run1": 3}}'}
    )
    event.refresh_from_db()
    assert good.status_code == 302 and event.column_mappings["Лист1"]["run1"] == 3


def test_entry_manual_edit_view(manager_client, event):
    services.ingest_snapshot(event.pk, _sheets(), now=T0)
    theta = event.entries.get(start_number=14)
    url = reverse("results:entry_result", kwargs={"pk": event.pk, "entry_id": theta.pk})
    manager_client.post(url, {"run1": "33.33", "run2": ""})
    theta.refresh_from_db()
    assert theta.run1_manual == "33.33" and theta.run2_manual is None


def test_live_fragment_returns_204_when_version_unchanged(manager_client, event):
    services.ingest_snapshot(event.pk, _sheets(), now=T0)
    event.refresh_from_db()
    url = reverse("results:live_fragment", kwargs={"pk": event.pk})
    assert manager_client.get(url).status_code == 200
    assert manager_client.get(url, {"v": event.version}).status_code == 204
    assert manager_client.get(reverse("results:live", kwargs={"pk": event.pk})).status_code == 200


def test_public_page_hidden_for_draft_and_when_disabled(client, event):
    url = reverse("results:public_live", kwargs={"token": event.public_token})
    assert client.get(url).status_code == 410  # черновик: страница честно говорит, что эфир недоступен
    services.set_status(event.pk, ResultsEvent.Status.RUNNING)
    assert client.get(url).status_code == 200
    ResultsEvent.objects.filter(pk=event.pk).update(public_enabled=False)
    response = client.get(url)
    assert response.status_code == 410 and "Трансляция недоступна" in response.content.decode()
    assert client.get(reverse("results:public_live", kwargs={"token": "нет-такого"})).status_code == 404


def test_plain_user_cannot_manage(client, event):
    client.force_login(User.objects.create(email="user@example.com", is_approved=True))
    assert client.get(reverse("results:event_list")).status_code == 403
    assert client.get(reverse("results:event_detail", kwargs={"pk": event.pk})).status_code == 403


# --- telegram -------------------------------------------------------------------------------------


@pytest.fixture
def tg_event(event):
    event.telegram_enabled = True
    event.telegram_channel = TelegramChat.objects.create(chat_id=-1001, type=TelegramChat.ChatType.CHANNEL, title="C")
    event.status = ResultsEvent.Status.RUNNING
    event.save()
    return event


def test_telegram_cycle_send_then_edit_then_rotate(tg_event):
    from results.telegram import publish

    calls = []
    counter = iter(range(100, 200))

    async def fake_call(method, **kwargs):
        calls.append((method, kwargs.get("message_id")))
        return SimpleNamespace(message_id=next(counter))

    def tick(sheets, at):
        services.ingest_snapshot(tg_event.pk, sheets, now=at)
        publish(tg_event.pk)  # в проде вызывает воркер через Celery

    with patch("results.telegram._call", fake_call):
        tick(_sheets(), T0)  # фокус: мальчики, 1 заезд
        pub = TelegramPublication.objects.get(event=tg_event)
        assert [c[0] for c in calls] == ["send_message"] * 3  # таблица, финиш, ссылка
        assert pub.table_key == "СЛГ|Мальчики 2014-2015 г.р.|1"
        calls.clear()

        tick(_set(_sheets(), THETA_ROW, RUN1, "34.20"), T0 + timedelta(seconds=5))
        assert [c[0] for c in calls] == ["edit_message_text", "edit_message_text"]  # таблица и финиш
        calls.clear()

        # 2-й заезд девочек: лист закрыт по 1-му заезду → фокус переходит, хвост пересоздаётся
        sheets = _set(_set(_sheets(), THETA_ROW, RUN1, "34.20"), 9, RUN2, "37.00")
        tick(sheets, T0 + timedelta(seconds=10))
        methods = [c[0] for c in calls]
        assert methods == ["delete_message", "delete_message", "send_message", "send_message", "send_message"]
        pub.refresh_from_db()
        assert pub.table_key == "СЛГ|Девочки 2014-2015 г.р.|2"


def test_telegram_skipped_without_channel(event):
    from results.telegram import publish

    services.ingest_snapshot(event.pk, _sheets(), now=T0)
    assert publish(event.pk) is False


# --- replay и linking -----------------------------------------------------------------------------


def test_replay_reproduces_focus_and_completion_from_snapshots(event):
    sheets = _sheets()
    services.ingest_snapshot(event.pk, copy.deepcopy(sheets), now=T0)
    services.ingest_snapshot(
        event.pk, _set(copy.deepcopy(sheets), THETA_ROW, RUN1, "34.2"), now=T0 + timedelta(seconds=5)
    )
    snapshots = [(s.taken_at, s.sheets()) for s in RawSnapshot.objects.filter(event=event).order_by("taken_at")]
    steps = list(replay(snapshots))
    assert len(steps) == 2
    assert steps[-1].focus.current.group_key == "СЛГ|Мальчики 2014-2015 г.р."
    assert steps[-1].protocol.group("СЛГ|Мальчики 2014-2015 г.р.").run_closed(1)


def test_link_entries_to_school_athletes(event):
    services.ingest_snapshot(event.pk, _sheets(), now=T0)
    anna = Athlete.objects.create(
        person=Person.objects.create(surname="Альфа", name="Анна", gender="female", date_of_birth="2014-03-01"),
        level="unknown",
    )
    Athlete.objects.create(person=Person.objects.create(surname="Бета", name="Вера", gender="female"), level="unknown")
    Athlete.objects.create(person=Person.objects.create(surname="Бета", name="Вера", gender="female"), level="unknown")
    Athlete.objects.create(  # однофамилица с другим годом рождения — не кандидат
        person=Person.objects.create(surname="Гамма", name="Галина", gender="female", date_of_birth="2010-01-01"),
        level="unknown",
    )
    report = link_event_entries(event)
    assert (report.linked, report.ambiguous) == (1, 1)
    assert event.entries.get(start_number=1).athlete == anna
    assert event.entries.get(start_number=3).athlete is None


def _xlsx_bytes(sheets) -> bytes:
    import io

    from openpyxl import Workbook

    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append([float(v) if isinstance(v, str) and v.replace(".", "", 1).isdigit() else v for v in row])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_uploaded_xlsx_preview_and_sync(manager_client):
    from django.core.files.uploadedfile import SimpleUploadedFile

    upload = SimpleUploadedFile("protocol.xlsx", _xlsx_bytes(protocols.format_b_split_names()))
    response = manager_client.post(
        reverse("results:event_create"),
        {
            "title": "XLSX",
            "source_kind": "upload",
            "source_file": upload,
            "poll_interval_sec": 2,
            "idle_interval_sec": 15,
            "finalize_timeout_sec": 300,
            "finalize_max_missing": 2,
        },
    )
    event = ResultsEvent.objects.get(title="XLSX")
    detail = response.url
    preview = manager_client.post(detail, {"action": "preview"})
    assert preview.status_code == 200
    protocol = preview.context["preview"]["protocol"]
    assert len(protocol.entries) == 4 and protocol.event_date == "02.04.2026"
    assert event.entries.count() == 0  # предпросмотр ничего не пишет
    manager_client.post(detail, {"action": "sync"})
    mark = event.entries.get(full_name="Мю Марк")
    assert mark.run1_raw == "40" and mark.run2_raw == "41"
    assert services.load_protocol(event).group("Слалом|Юноши 2012-2013 г.р.").entries[0].run1.ms == 40000
