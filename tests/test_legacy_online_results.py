"""Вывод старой интеграции Online Results: запуск закрыт, история и действующие публичные ссылки читаются."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from api.models import PublicStreamAccess, StreamRun, WebhookEvent

pytestmark = pytest.mark.django_db


def _stale_run(stream_id="stale-stream"):
    return StreamRun.objects.create(
        stream_id=stream_id,
        protocol_link="https://docs.google.com/spreadsheets/d/test",
        callback_url="https://example.com/callback",
        status=StreamRun.Status.RUNNING,
        started_at=timezone.now() - timedelta(hours=2),
        external_response_json={"stream_output": {"overall_stats_lines": ["итоги"]}},
    )


def test_launch_form_and_direct_post_do_not_create_streams(client_for):
    client = client_for("admin")
    page = client.get(reverse("online-results-stream-runs"))
    assert page.status_code == 200 and "запуск новых потоков закрыт" in page.content.decode()
    with patch("api.views.launch_online_results_stream_task.delay") as delay:
        response = client.post(
            reverse("online-results-stream-runs"), {"protocol_link": "https://docs.google.com/spreadsheets/d/x"}
        )
    assert response.status_code == 302 and not delay.called
    assert StreamRun.objects.count() == 0


def test_live_state_does_not_recover_stale_stream(client_for):
    run = _stale_run()
    with patch("api.services.request") as http:
        response = client_for("admin").get(reverse("online-results-live-state"), {"stream_id": run.stream_id})
    assert response.status_code == 200
    assert StreamRun.objects.count() == 1 and not http.urlopen.called


def test_hard_refresh_is_refused_and_keeps_history(client_for):
    run = _stale_run()
    WebhookEvent.objects.create(stream_id=run.stream_id, event_type="tick", payload_json={}, payload_hash="h" * 64)
    response = client_for("admin").post(reverse("online-results-hard-refresh"), {"stream_id": run.stream_id})
    assert response.status_code == 410
    assert WebhookEvent.objects.filter(stream_id=run.stream_id).count() == 1 and StreamRun.objects.count() == 1


def test_launch_service_marks_pending_run_failed_when_disabled():
    from api.services import launch_online_results_stream

    run = StreamRun.objects.create(
        stream_id="pending-abc", protocol_link="https://x", callback_url="https://e", status=StreamRun.Status.PENDING
    )
    launch_online_results_stream(run.id)
    run.refresh_from_db()
    assert run.status == StreamRun.Status.FAILED and "закрыт" in run.last_error


def test_existing_public_link_still_readable(client):
    run = _stale_run("public-stream")
    access = PublicStreamAccess.objects.create(
        stream_run=run, token="legacy-token", expires_at=timezone.now() + timedelta(days=2), is_active=True
    )
    assert client.get(reverse("online-results-live-public", kwargs={"token": access.token})).status_code == 200
    state = client.get(reverse("online-results-live-public-state", kwargs={"token": access.token}))
    assert state.status_code == 200 and StreamRun.objects.count() == 1


def test_menu_has_no_legacy_items(client_for):
    html = client_for("superuser").get(reverse("school:index")).content.decode()
    assert "Online Results: Потоки" not in html and "Webhook события" not in html
    assert reverse("results:event_list") in html
