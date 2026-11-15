"""Регрессия по найденным дефектам: ручная правка и пересчёт мест, смена XLSX на ходу, битый файл,
отзыв публичного доступа, прогресс и финальное состояние, ручной состав."""

import copy
import io
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group as AuthGroup
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from results import services
from results.core.values import InvalidResult
from results.models import EventLog, ResultEntry, ResultsEvent
from results.state import build_state
from results.worker import EventRunner
from users.constants import MANAGER_GROUP_NAME
from users.models import User

from .fixtures import protocols

pytestmark = pytest.mark.django_db
T0 = timezone.now().replace(microsecond=0)


def xlsx(sheets) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    workbook.remove(workbook.active)
    for name, rows in sheets.items():
        sheet = workbook.create_sheet(name)
        for row in rows:
            sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def manager(client):
    user = User.objects.create(email="manager@example.com", is_approved=True)
    user.groups.add(AuthGroup.objects.get_or_create(name=MANAGER_GROUP_NAME)[0])
    client.force_login(user)
    return client


@pytest.fixture
def event():
    return ResultsEvent.objects.create(title="Кубок", source_kind=ResultsEvent.SourceKind.MANUAL)


def girls(event, now=None):
    state = build_state(ResultsEvent.objects.get(pk=event.pk), now=now)
    return next(g for g in state.groups if g.name.startswith("Девочки"))


def row(group, name):
    return next(r for r in group.rows if r.full_name == name)


# --- 1. Ручная правка: итог, места, отставания, сброс, статусы, валидация -------------------------


def test_manual_run_change_recomputes_total_place_and_gap(event):
    services.ingest_snapshot(event.pk, protocols.format_a_two_runs(), now=T0)
    before = girls(event)
    assert row(before, "Альфа Анна").total == "1:11.10" and row(before, "Альфа Анна").place == 2

    anna = event.entries.get(full_name="Альфа Анна")
    services.set_manual_result(anna.pk, 1, "30.00")
    after = girls(event)
    assert row(after, "Альфа Анна").total == "1:06.00"
    assert row(after, "Альфа Анна").place == 1 and row(after, "Альфа Анна").gap == "0.00"
    assert row(after, "Бета Вера").place == 2 and row(after, "Бета Вера").gap == "+4.40"

    services.set_manual_result(anna.pk, 1, None)  # сброс правки — снова данные протокола
    reset = girls(event)
    assert row(reset, "Альфа Анна").total == "1:11.10" and row(reset, "Альфа Анна").place == 2


def test_manual_change_single_run_and_statuses(event):
    services.ingest_snapshot(event.pk, protocols.format_a_lite_single_run(), now=T0)
    iya = event.entries.get(full_name="Йота Ия")
    services.set_manual_result(iya.pk, 1, "46.00")
    group = build_state(ResultsEvent.objects.get(pk=event.pk)).groups[0]
    assert row(group, "Йота Ия").total == "46.00" and row(group, "Йота Ия").place == 1

    services.set_manual_result(iya.pk, 1, "150")  # судейский код → DNF
    group = build_state(ResultsEvent.objects.get(pk=event.pk)).groups[0]
    assert row(group, "Йота Ия").total == "DNF" and row(group, "Йота Ия").place == 2
    for status in ("DNS", "dsq"):
        services.set_manual_result(iya.pk, 1, status)
        assert row(build_state(ResultsEvent.objects.get(pk=event.pk)).groups[0], "Йота Ия").total == status.upper()


def test_manual_status_in_second_run_dominates_total(event):
    services.ingest_snapshot(event.pk, protocols.format_a_two_runs(), now=T0)
    anna = event.entries.get(full_name="Альфа Анна")
    services.set_manual_result(anna.pk, 2, "DSQ")
    group = girls(event)
    assert row(group, "Альфа Анна").total == "DSQ" and row(group, "Альфа Анна").gap == ""


def test_invalid_manual_value_rejected_and_previous_kept(event, manager):
    services.ingest_snapshot(event.pk, protocols.format_a_two_runs(), now=T0)
    anna = event.entries.get(full_name="Альфа Анна")
    with pytest.raises(InvalidResult):
        services.set_manual_result(anna.pk, 1, "НЕ ВРЕМЯ")
    url = reverse("results:entry_result", kwargs={"pk": event.pk, "entry_id": anna.pk})
    response = manager.post(url, {"run1": "НЕ ВРЕМЯ", "run2": "40.00"}, follow=True)
    text = response.content.decode()
    assert "не время и не статус" in text and "Изменения не сохранены" in text
    anna.refresh_from_db()
    assert anna.run1_manual is None and anna.run2_manual is None  # и второй заезд не сохранился
    assert row(girls(event), "Альфа Анна").total == "1:11.10"


# --- 2. Смена XLSX при работающем воркере, запоздавшее чтение, битый файл -------------------------


@pytest.fixture
def upload_event(settings, tmp_path):
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"location": str(tmp_path)}},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    event = ResultsEvent.objects.create(
        title="XLSX", source_kind=ResultsEvent.SourceKind.UPLOAD, status=ResultsEvent.Status.RUNNING
    )
    event.source_file.save("a.xlsx", ContentFile(xlsx(protocols.format_a_two_runs())))
    return event


def test_worker_switches_to_replaced_xlsx_without_restart(upload_event, manager):
    runner = EventRunner(upload_event.pk)
    with patch("results.worker.enqueue_publication"):
        runner.tick()
        assert upload_event.entries.filter(removed=False).count() == 8

        new_file = SimpleUploadedFile("b.xlsx", xlsx(protocols.format_b_split_names()))
        form = {
            "title": "XLSX",
            "source_kind": "upload",
            "source_file": new_file,
            "poll_interval_sec": 2,
            "idle_interval_sec": 15,
            "finalize_timeout_sec": 300,
            "finalize_max_missing": 2,
        }
        assert manager.post(reverse("results:event_update", kwargs={"pk": upload_event.pk}), form).status_code == 302
        upload_event.refresh_from_db()
        assert upload_event.source_revision == 1

        runner.tick()  # тот же поток воркера — уже читает новый файл
    names = set(upload_event.entries.filter(removed=False).values_list("full_name", flat=True))
    assert names == {"Мю Марк", "Ню Никита", "Кси Ксения", "Омикрон Ольга"}


def test_late_read_of_old_source_does_not_overwrite_new_data(upload_event):
    started_old = timezone.now()
    old_sheets = protocols.format_a_two_runs()
    # пока «старое» чтение шло, источник сменили и прочитали новый
    ResultsEvent.objects.filter(pk=upload_event.pk).update(source_revision=1)
    fresh = services.ingest_snapshot(
        upload_event.pk,
        protocols.format_b_split_names(),
        source_revision=1,
        fetched_at=started_old + timedelta(seconds=1),
    )
    assert fresh.changed
    late = services.ingest_snapshot(upload_event.pk, old_sheets, source_revision=0, fetched_at=started_old)
    assert late.stale
    # тот же источник, но чтение началось раньше уже применённого — тоже отбрасывается
    sheets = copy.deepcopy(protocols.format_b_split_names())
    sheets["Слалом"][5][4] = "99.99"
    older = services.ingest_snapshot(upload_event.pk, sheets, source_revision=1, fetched_at=started_old)
    assert older.stale
    names = set(upload_event.entries.filter(removed=False).values_list("full_name", flat=True))
    assert "Альфа Анна" not in names and upload_event.entries.get(full_name="Мю Марк").run1_raw == "40.00"


def test_mapping_change_reparses_same_file(upload_event, manager):
    services.ingest_snapshot(upload_event.pk, protocols.legacy_without_header(), source_revision=0)
    url = reverse("results:event_detail", kwargs={"pk": upload_event.pk})
    manager.post(
        url,
        {
            "action": "mappings",
            "mappings": '{"Лист1": {"start_number": 0, "full_name": 1, "club": 2, "run1": 3, "header_row": 6}}',
        },
    )
    upload_event.refresh_from_db()
    services.ingest_snapshot(
        upload_event.pk, protocols.legacy_without_header(), source_revision=upload_event.source_revision
    )
    assert all(e.runs_count == 1 for e in upload_event.entries.filter(removed=False))


def test_corrupt_xlsx_rejected_on_upload_and_handled_in_preview_sync_and_worker(upload_event, manager):
    services.ingest_snapshot(upload_event.pk, protocols.format_a_two_runs(), source_revision=0)
    form = {
        "title": "XLSX",
        "source_kind": "upload",
        "source_file": SimpleUploadedFile("bad.xlsx", b"PK\x03\x04 not really a zip"),
        "poll_interval_sec": 2,
        "idle_interval_sec": 15,
        "finalize_timeout_sec": 300,
        "finalize_max_missing": 2,
    }
    response = manager.post(reverse("results:event_update", kwargs={"pk": upload_event.pk}), form)
    assert response.status_code == 200 and "повреждён" in str(response.context["form"].errors["source_file"])
    upload_event.refresh_from_db()
    assert upload_event.source_file.name.endswith("a.xlsx") and upload_event.source_revision == 0

    # файл испортился уже в хранилище
    with upload_event.source_file.open("wb") as handle:
        handle.write(b"garbage")
    detail = reverse("results:event_detail", kwargs={"pk": upload_event.pk})
    for action in ("preview", "sync"):
        response = manager.post(detail, {"action": action}, follow=True)
        assert response.status_code == 200 and "повреждён" in response.content.decode()
    with patch("results.worker.enqueue_publication"):
        EventRunner(upload_event.pk).tick()
    upload_event.refresh_from_db()
    assert "повреждён" in upload_event.last_error
    assert upload_event.entries.filter(removed=False).count() == 8  # последний корректный протокол на месте


# --- 3. Live и пульт: отзыв доступа, финальное состояние, автообновление пульта -------------------


def test_open_public_tab_sees_revoked_access_and_draft(client, event):
    services.ingest_snapshot(event.pk, protocols.format_a_two_runs(), now=T0)
    services.set_status(event.pk, ResultsEvent.Status.RUNNING)
    fragment = reverse("results:public_fragment", kwargs={"token": event.public_token})
    assert client.get(fragment).status_code == 200
    ResultsEvent.objects.filter(pk=event.pk).update(public_enabled=False)
    response = client.get(fragment, {"v": 1})
    assert response.status_code == 410 and "Трансляция недоступна" in response.content.decode()
    ResultsEvent.objects.filter(pk=event.pk).update(public_enabled=True, status=ResultsEvent.Status.DRAFT)
    assert client.get(fragment).status_code == 410
    assert client.get(reverse("results:public_fragment", kwargs={"token": "missing"})).status_code == 404


def test_finished_event_shows_final_results_not_coming_soon(client, event):
    services.ingest_snapshot(event.pk, protocols.format_a_two_runs(), now=T0)
    services.set_status(event.pk, ResultsEvent.Status.FINISHED)
    html = client.get(reverse("results:public_live", kwargs={"token": event.public_token})).content.decode()
    assert "Итоговые результаты" in html and "скоро начнётся" not in html and "Сейчас:" not in html


def test_panel_fragments_report_status_and_log(manager, event):
    services.ingest_snapshot(event.pk, protocols.format_a_two_runs(), now=T0)
    services.record_error(event.pk, "Google API: HTTP 500")
    status = manager.get(reverse("results:panel_fragment", kwargs={"pk": event.pk}), {"part": "status"})
    assert status.status_code == 200 and "HTTP 500" in status.content.decode()
    log = manager.get(reverse("results:panel_fragment", kwargs={"pk": event.pk}), {"part": "log"})
    assert "Протокол загружен" in log.content.decode()


# --- 4. Прогресс, перерыв, причина места -----------------------------------------------------------


def test_progress_separates_finishers_from_dns_and_break_label(event):
    services.ingest_snapshot(event.pk, protocols.format_a_lite_single_run(), now=T0)
    lisa = event.entries.get(full_name="Лямбда Лиза")
    services.set_manual_result(lisa.pk, 1, "DNS")
    group = build_state(ResultsEvent.objects.get(pk=event.pk)).groups[0]
    assert (group.finishers, group.out, group.waiting, group.total) == (2, 1, 0, 3)

    sheets = copy.deepcopy(protocols.format_a_two_runs())
    for index in (7, 8, 10):
        sheets["СЛГ"][index][5] = ""  # 2-й заезд девочек ещё не начат
    other = ResultsEvent.objects.create(title="B", source_kind=ResultsEvent.SourceKind.MANUAL)
    services.ingest_snapshot(other.pk, sheets, now=T0)
    assert girls(other).phase_label == "1 заезд закрыт · перерыв"


def test_judge_note_reason_is_visible(event):
    services.ingest_snapshot(event.pk, protocols.format_a_two_runs(), now=T0)
    boys = next(g for g in build_state(ResultsEvent.objects.get(pk=event.pk)).groups if g.name.startswith("Мальчики"))
    zeta, eta = row(boys, "Зета Захар"), row(boys, "Эта Эрик")
    assert zeta.place > eta.place  # 33.80 с пометкой ниже 1:02.50 без пометки — правило сортировки
    assert zeta.rank_note == "ниже чистых времён: пометка судьи" and zeta.note == "касание флага"


# --- 5. Ручной состав -------------------------------------------------------------------------------


def test_manual_roster_bulk_add_and_duplicates(manager, event):
    url = reverse("results:event_detail", kwargs={"pk": event.pk})
    assert "Что нужно для запуска" in manager.get(url).content.decode()
    bulk = "Девочки 2014\n1; Альфа Анна; СШ Север; 2014\n2; Бета Вера; СШ Юг\n\nМальчики\n11; Гамма Глеб; СШ Юг; 2013"
    manager.post(url, {"action": "add_bulk", "bulk": bulk, "runs_count": "2"})
    assert event.entries.filter(removed=False).count() == 3 and event.groups.filter(removed=False).count() == 2
    response = manager.post(
        url, {"action": "add_bulk", "bulk": "Мальчики\n11; Дубль; X", "runs_count": "2"}, follow=True
    )
    assert "уже занят" in response.content.decode() and event.entries.count() == 3
    bad = manager.post(url, {"action": "add_bulk", "bulk": "1; Без группы", "runs_count": "2"}, follow=True)
    assert "сначала укажите группу" in bad.content.decode()

    anna = event.entries.get(full_name="Альфа Анна")
    services.set_manual_result(anna.pk, 1, "35.00")
    assert row(build_state(ResultsEvent.objects.get(pk=event.pk)).groups[0], "Альфа Анна").run1 == "35.00"
    manager.post(reverse("results:entry_result", kwargs={"pk": event.pk, "entry_id": anna.pk}), {"remove": "1"})
    assert ResultEntry.objects.get(pk=anna.pk).removed
    assert EventLog.objects.filter(event=event, message__startswith="Убран участник").exists()
