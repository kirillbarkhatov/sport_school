import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import StreamRun, WebhookEvent
from api.services import launch_online_results_stream, process_online_results_webhook_event
from whatsapp.models import WhatsAppChat, WhatsAppChatMember


class WhatsAppChatSyncViewTests(APITestCase):
    def setUp(self):
        self.url = reverse("api:whatsapp-chat-members")

    def test_sync_creates_and_updates_members(self):
        synced_at = timezone.now()
        payload = {
            "group_name": "Test WhatsApp Chat",
            "synced_at": synced_at.isoformat(),
            "members": [
                {
                    "member_id": "1",
                    "phone": "+10000000001",
                    "push_name": "Alice",
                    "is_admin": True,
                    "is_super_admin": False,
                },
                {
                    "member_id": "2",
                    "phone": "+10000000002",
                    "push_name": "Bob",
                    "is_admin": False,
                    "is_super_admin": False,
                },
            ],
        }

        response = self.client.post(self.url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        chat = WhatsAppChat.objects.get(name=payload["group_name"])
        self.assertEqual(chat.last_synced_at, synced_at)
        self.assertEqual(chat.members.count(), 2)
        self.assertEqual(chat.members.filter(is_active=True).count(), 2)

        alice = WhatsAppChatMember.objects.get(chat=chat, member_id="1")
        self.assertTrue(alice.is_admin)
        self.assertFalse(alice.is_super_admin)
        self.assertEqual(alice.last_seen_at, synced_at)
        self.assertIsNone(alice.left_at)

        second_sync_time = synced_at + timedelta(hours=1)
        second_payload = {
            "group_name": "Test WhatsApp Chat",
            "synced_at": second_sync_time.isoformat(),
            "members": [
                {
                    "member_id": "2",
                    "phone": "+10000000002",
                    "push_name": "Bob",
                    "is_admin": True,
                    "is_super_admin": True,
                }
            ],
        }

        response = self.client.post(self.url, second_payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        chat.refresh_from_db()
        self.assertEqual(chat.last_synced_at, second_sync_time)
        self.assertEqual(chat.members.count(), 2)  # прежние записи остаются
        self.assertEqual(chat.members.filter(is_active=True).count(), 1)

        alice.refresh_from_db()
        self.assertFalse(alice.is_active)
        self.assertEqual(alice.left_at, second_sync_time)

        bob = WhatsAppChatMember.objects.get(chat=chat, member_id="2")
        self.assertTrue(bob.is_active)
        self.assertEqual(bob.last_seen_at, second_sync_time)
        self.assertTrue(bob.is_admin)
        self.assertTrue(bob.is_super_admin)


@override_settings(ONLINE_RESULTS_WEBHOOK_SECRET="test_secret")
class OnlineResultsWebhookViewTests(APITestCase):
    def setUp(self):
        self.url = reverse("online-results-webhook")

    @staticmethod
    def _signature(body: bytes, secret: str = "test_secret") -> str:
        digest = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        return f"sha256={digest}"

    @patch("api.views.process_online_results_webhook_event_task.delay")
    def test_valid_signature_returns_200_and_creates_record(self, _delay_mock):
        payload = {
            "stream_id": "stream-1",
            "event_type": "results.updated",
            "event_time": "2026-03-05T10:00:00Z",
            "result_id": 123,
        }
        body = json.dumps(payload).encode("utf-8")

        response = self.client.generic(
            "POST",
            self.url,
            data=body,
            content_type="application/json",
            HTTP_X_ONLINE_RESULTS_SIGNATURE=self._signature(body),
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(WebhookEvent.objects.count(), 1)
        event = WebhookEvent.objects.get()
        self.assertEqual(event.stream_id, "stream-1")
        self.assertEqual(event.event_type, "results.updated")
        self.assertEqual(event.payload_hash, hashlib.sha256(body).hexdigest())

    @patch("api.views.process_online_results_webhook_event_task.delay")
    def test_valid_signature_with_charset_content_type_returns_200(self, _delay_mock):
        payload = {"stream_id": "stream-charset", "event_type": "stream_started"}
        body = json.dumps(payload).encode("utf-8")
        response = self.client.generic(
            "POST",
            self.url,
            data=body,
            content_type="application/json; charset=utf-8",
            HTTP_X_ONLINE_RESULTS_SIGNATURE=self._signature(body),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(WebhookEvent.objects.count(), 1)

    def test_get_method_returns_405(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    @patch("api.views.process_online_results_webhook_event_task.delay")
    def test_invalid_signature_returns_401(self, _delay_mock):
        body = b'{"stream_id":"stream-1","event_type":"results.updated"}'

        response = self.client.generic(
            "POST",
            self.url,
            data=body,
            content_type="application/json",
            HTTP_X_ONLINE_RESULTS_SIGNATURE="sha256=" + ("0" * 64),
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    @patch("api.views.process_online_results_webhook_event_task.delay")
    def test_missing_signature_returns_401(self, _delay_mock):
        body = b'{"stream_id":"stream-1","event_type":"results.updated"}'
        response = self.client.generic(
            "POST",
            self.url,
            data=body,
            content_type="application/json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    @patch("api.views.process_online_results_webhook_event_task.delay")
    def test_invalid_json_returns_400(self, _delay_mock):
        body = b"{invalid-json"
        response = self.client.generic(
            "POST",
            self.url,
            data=body,
            content_type="application/json",
            HTTP_X_ONLINE_RESULTS_SIGNATURE=self._signature(body),
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    @patch("api.views.process_online_results_webhook_event_task.delay")
    def test_duplicate_payload_returns_200_and_keeps_single_record(self, _delay_mock):
        body = b'{"stream_id":"stream-dup","event_type":"results.updated"}'
        signature = self._signature(body)

        first_response = self.client.generic(
            "POST",
            self.url,
            data=body,
            content_type="application/json",
            HTTP_X_ONLINE_RESULTS_SIGNATURE=signature,
        )
        second_response = self.client.generic(
            "POST",
            self.url,
            data=body,
            content_type="application/json",
            HTTP_X_ONLINE_RESULTS_SIGNATURE=signature,
        )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(WebhookEvent.objects.count(), 1)


class OnlineResultsPagesTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create(
            email="admin@example.com",
            is_staff=True,
            is_approved=True,
        )
        self.user.set_password("test-password")
        self.user.save(update_fields=["password"])
        self.client.login(email="admin@example.com", password="test-password")

    def test_stream_runs_page_available(self):
        response = self.client.get(reverse("online-results-stream-runs"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_live_page_available(self):
        response = self.client.get(reverse("online-results-live"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_live_state_returns_payload(self):
        run = StreamRun.objects.create(
            stream_id="stream-live-state",
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
            external_response_json={"stream_output": {"overall_stats_lines": ["x"]}},
        )
        response = self.client.get(
            reverse("online-results-live-state"),
            data={"stream_id": run.stream_id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["stream"]["stream_id"], run.stream_id)

    @patch("api.views.launch_online_results_stream_task.delay")
    def test_stream_run_create_from_page(self, delay_mock):
        payload = {
            "protocol_link": "https://docs.google.com/spreadsheets/d/test",
        }
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("online-results-stream-runs"), data=payload)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertEqual(StreamRun.objects.count(), 1)
        run = StreamRun.objects.get()
        self.assertEqual(run.protocol_link, payload["protocol_link"])
        self.assertTrue(run.stream_id.startswith("pending-"))
        delay_mock.assert_called_once_with(run.id)

    @override_settings(ONLINE_RESULTS_WEBHOOK_PUBLIC_BASE_URL="http://host.docker.internal:8000")
    @patch("api.views.launch_online_results_stream_task.delay")
    def test_stream_run_create_uses_public_webhook_base_url(self, delay_mock):
        payload = {
            "protocol_link": "https://docs.google.com/spreadsheets/d/test",
        }
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("online-results-stream-runs"), data=payload)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        run = StreamRun.objects.get()
        self.assertEqual(
            run.callback_url,
            "http://host.docker.internal:8000/integrations/online-results/webhook/",
        )
        delay_mock.assert_called_once_with(run.id)

    def test_webhook_events_page_shows_events(self):
        WebhookEvent.objects.create(
            stream_id="stream-events",
            event_type="results.updated",
            payload_json={"result_id": 1},
            payload_hash="a" * 64,
        )
        response = self.client.get(reverse("online-results-webhook-events"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "stream-events")

    @patch("api.views.stop_online_results_stream_task.delay")
    def test_stream_run_stop_from_page(self, stop_delay_mock):
        run = StreamRun.objects.create(
            stream_id="stream-stop-me",
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
            status=StreamRun.Status.RUNNING,
            created_by=self.user,
        )
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("online-results-stream-runs"),
                data={"action": "stop", "run_id": str(run.id)},
            )
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        stop_delay_mock.assert_called_once()
        called_run_id = stop_delay_mock.call_args.args[0]
        self.assertEqual(called_run_id, run.id)


@override_settings(
    ONLINE_RESULTS_STREAM_START_URL="http://localhost:8002/v1/streams",
    ONLINE_RESULTS_STREAM_TIMEOUT_SEC=15,
    ONLINE_RESULTS_STREAM_AUTH_TOKEN="",
    ONLINE_RESULTS_WEBHOOK_SECRET="test_secret",
    ONLINE_RESULTS_STREAM_POLL_INTERVAL_SEC=1.0,
    ONLINE_RESULTS_STREAM_REFRESH_TITLES_EVERY=120,
    ONLINE_RESULTS_STREAM_FINALIZE_TIMEOUT_SEC=300,
    ONLINE_RESULTS_STREAM_FINALIZE_MAX_MISSING=2,
    ONLINE_RESULTS_STREAM_WORKER_PRINT=False,
)
class OnlineResultsServicesTests(APITestCase):
    def _create_run(self, stream_id: str = "pending-123") -> StreamRun:
        return StreamRun.objects.create(
            stream_id=stream_id,
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
        )

    def test_launch_stream_keeps_running_status_until_webhook_completion(self):
        run = self._create_run()
        response_body = json.dumps({"stream_id": "remote-1"}).encode("utf-8")
        response = MagicMock()
        response.read.return_value = response_body
        response.__enter__.return_value = response
        response.__exit__.return_value = False

        with patch("api.services.request.urlopen", return_value=response):
            launch_online_results_stream(run.id)

        run.refresh_from_db()
        self.assertEqual(run.stream_id, "remote-1")
        self.assertEqual(run.status, StreamRun.Status.RUNNING)
        self.assertIsNone(run.finished_at)
        self.assertIn("start_response", run.external_response_json)

    def test_stream_completed_event_marks_run_success(self):
        run = self._create_run(stream_id="remote-2")
        event_time = timezone.now()
        event = WebhookEvent.objects.create(
            stream_id="remote-2",
            event_type="stream_completed",
            event_time=event_time,
            payload_json={"payload": {"completed_at": event_time.isoformat()}},
            payload_hash="1" * 64,
        )

        process_online_results_webhook_event(event.id)

        run.refresh_from_db()
        self.assertEqual(run.status, StreamRun.Status.SUCCESS)
        self.assertIsNotNone(run.finished_at)

    def test_stream_error_event_marks_run_failed_and_persists_error(self):
        run = self._create_run(stream_id="remote-3")
        event = WebhookEvent.objects.create(
            stream_id="remote-3",
            event_type="stream_error",
            payload_json={"payload": {"error": "boom"}},
            payload_hash="2" * 64,
        )

        process_online_results_webhook_event(event.id)

        run.refresh_from_db()
        self.assertEqual(run.status, StreamRun.Status.FAILED)
        self.assertEqual(run.last_error, "boom")
        self.assertIsNotNone(run.finished_at)

    def test_stream_stopped_event_marks_run_stopped(self):
        run = self._create_run(stream_id="remote-4")
        event = WebhookEvent.objects.create(
            stream_id="remote-4",
            event_type="stream_stopped",
            payload_json={"payload": {"reason": "external_stop"}},
            payload_hash="3" * 64,
        )

        process_online_results_webhook_event(event.id)

        run.refresh_from_db()
        self.assertEqual(run.status, StreamRun.Status.STOPPED)
        self.assertEqual(run.last_error, "external_stop")
        self.assertIsNotNone(run.finished_at)

    def test_result_and_stats_events_are_saved_to_stream_state(self):
        run = self._create_run(stream_id="remote-5")
        events = [
            WebhookEvent.objects.create(
                stream_id="remote-5",
                event_type="result_updated",
                payload_json={"payload": {"lines": ["line 1", "line 2"]}},
                payload_hash="4" * 64,
            ),
            WebhookEvent.objects.create(
                stream_id="remote-5",
                event_type="group_table_updated",
                payload_json={
                    "payload": {
                        "group_key": "sheet|group",
                        "sheet_name": "sheet",
                        "group_name": "group",
                        "lines": ["table row"],
                    }
                },
                payload_hash="5" * 64,
            ),
            WebhookEvent.objects.create(
                stream_id="remote-5",
                event_type="overall_completed",
                payload_json={"payload": {"lines": ["club stats"]}},
                payload_hash="6" * 64,
            ),
        ]
        for event in events:
            process_online_results_webhook_event(event.id)

        run.refresh_from_db()
        stream_output = run.external_response_json.get("stream_output", {})
        self.assertEqual(stream_output.get("last_result_lines"), ["line 1", "line 2"])
        self.assertEqual(
            stream_output.get("latest_group_tables", {}).get("sheet|group", {}).get("lines"),
            ["table row"],
        )
        self.assertEqual(stream_output.get("overall_stats_lines"), ["club stats"])

    def test_group_table_structured_data_is_saved(self):
        run = self._create_run(stream_id="remote-structured")
        event = WebhookEvent.objects.create(
            stream_id="remote-structured",
            event_type="group_table_updated",
            payload_json={
                "payload": {
                    "group_key": "s|g",
                    "sheet_name": "sheet",
                    "group_name": "group",
                    "lines": ["line"],
                    "lines_plain": ["line_plain"],
                    "data": {"headers": ["h1"], "rows": [{"v": 1}]},
                }
            },
            payload_hash="7" * 64,
        )
        process_online_results_webhook_event(event.id)
        run.refresh_from_db()
        latest = run.external_response_json.get("stream_output", {}).get("latest_group_tables", {}).get("s|g", {})
        self.assertEqual(latest.get("lines_plain"), ["line_plain"])
        self.assertEqual(latest.get("data", {}).get("headers"), ["h1"])

    def test_stop_stream_marks_run_stopped(self):
        run = self._create_run(stream_id="remote-stop")
        run.status = StreamRun.Status.RUNNING
        run.save(update_fields=["status", "updated_at"])

        stop_response = MagicMock()
        stop_response.read.return_value = json.dumps({"status": "stopped"}).encode("utf-8")
        stop_response.__enter__.return_value = stop_response
        stop_response.__exit__.return_value = False

        with patch("api.services.request.urlopen", return_value=stop_response) as urlopen_mock:
            from api.services import stop_online_results_stream

            stop_online_results_stream(run.id, reason="manual_stop_test")

        run.refresh_from_db()
        self.assertEqual(run.status, StreamRun.Status.STOPPED)
        self.assertIn("manual_stop_test", run.last_error)
        self.assertIsNotNone(run.finished_at)
        called_url = urlopen_mock.call_args.args[0].full_url
        self.assertTrue(called_url.endswith("/v1/streams/remote-stop/stop"))

    def test_launch_stream_auto_stops_duplicate_running_stream(self):
        old_run = StreamRun.objects.create(
            stream_id="remote-old",
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
            status=StreamRun.Status.RUNNING,
        )
        new_run = self._create_run(stream_id="pending-new")

        stop_response = MagicMock()
        stop_response.read.return_value = b"{}"
        stop_response.__enter__.return_value = stop_response
        stop_response.__exit__.return_value = False

        start_response = MagicMock()
        start_response.read.return_value = json.dumps({"stream_id": "remote-new"}).encode("utf-8")
        start_response.__enter__.return_value = start_response
        start_response.__exit__.return_value = False

        with patch("api.services.request.urlopen", side_effect=[stop_response, start_response]):
            launch_online_results_stream(new_run.id)

        old_run.refresh_from_db()
        new_run.refresh_from_db()
        self.assertEqual(old_run.status, StreamRun.Status.STOPPED)
        self.assertEqual(new_run.status, StreamRun.Status.RUNNING)
        self.assertEqual(new_run.stream_id, "remote-new")
