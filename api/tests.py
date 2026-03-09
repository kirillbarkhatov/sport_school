import hashlib
import hmac
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import MagicMock

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import PublicStreamAccess, StreamRun, WebhookEvent
from api.services import launch_online_results_stream, process_online_results_webhook_event
from api.telegram_streaming import enable_stream_telegram_publication
from bot.models import TelegramChat, TelegramParticipant
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

    @patch("api.views._probe_online_results_stream_state", return_value={"ok": True, "found": True, "status": "running"})
    def test_soft_refresh_endpoint_returns_payload(self, _probe_mock):
        run = StreamRun.objects.create(
            stream_id="stream-soft-refresh",
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
            status=StreamRun.Status.RUNNING,
            created_by=self.user,
        )
        response = self.client.post(
            reverse("online-results-soft-refresh"),
            data={"stream_id": run.stream_id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertTrue(payload.get("soft_refreshed"))
        self.assertEqual(payload.get("resolved_stream_id"), run.stream_id)

    @patch("api.views.reset_online_results_stream_state")
    @patch("api.views._probe_online_results_stream_state", return_value={"ok": True, "found": True, "status": "running"})
    def test_hard_refresh_endpoint_restarts_and_returns_new_stream(self, _probe_mock, reset_mock):
        run = StreamRun.objects.create(
            stream_id="stream-hard-refresh-old",
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
            status=StreamRun.Status.RUNNING,
            created_by=self.user,
        )
        replacement = StreamRun.objects.create(
            stream_id="stream-hard-refresh-new",
            protocol_link=run.protocol_link,
            callback_url=run.callback_url,
            status=StreamRun.Status.RUNNING,
            created_by=self.user,
        )
        reset_mock.return_value = replacement

        response = self.client.post(
            reverse("online-results-hard-refresh"),
            data={"stream_id": run.stream_id},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertTrue(payload.get("hard_refreshed"))
        self.assertEqual(payload.get("previous_stream_id"), run.stream_id)
        self.assertEqual(payload.get("resolved_stream_id"), replacement.stream_id)

    @override_settings(ONLINE_RESULTS_STREAM_RESUME_STALE_SEC=0)
    @patch("api.views._probe_online_results_stream_state", return_value={"ok": True, "found": False})
    @patch("api.views.launch_online_results_stream")
    def test_live_state_recovers_stale_stream_and_returns_resolved_stream_id(self, launch_mock, _probe_mock):
        run = StreamRun.objects.create(
            stream_id="stale-stream-id",
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
            status=StreamRun.Status.RUNNING,
            started_at=timezone.now() - timedelta(minutes=10),
            created_by=self.user,
            launch_payload_json={"poll_interval_sec": 2.0},
        )

        def _launch_side_effect(run_id):
            new_run = StreamRun.objects.get(id=run_id)
            new_run.stream_id = "recovered-stream-id"
            new_run.status = StreamRun.Status.RUNNING
            new_run.save(update_fields=["stream_id", "status", "updated_at"])

        launch_mock.side_effect = _launch_side_effect
        response = self.client.get(reverse("online-results-live-state"), data={"stream_id": run.stream_id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(payload["resolved_stream_id"], "recovered-stream-id")
        self.assertEqual(payload["stream"]["stream_id"], "recovered-stream-id")
        self.assertTrue(
            StreamRun.objects.filter(protocol_link=run.protocol_link, stream_id="recovered-stream-id").exists()
        )

    @override_settings(ONLINE_RESULTS_STREAM_RESUME_STALE_SEC=0, ONLINE_RESULTS_STREAM_RECOVERY_COOLDOWN_SEC=60)
    @patch("api.views._probe_online_results_stream_state", return_value={"ok": True, "found": False})
    @patch("api.views.launch_online_results_stream")
    def test_live_state_reuses_recent_recovery_run_without_creating_new_one(self, launch_mock, _probe_mock):
        stale_run = StreamRun.objects.create(
            stream_id="stale-with-cooldown",
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
            status=StreamRun.Status.RUNNING,
            started_at=timezone.now() - timedelta(minutes=10),
            created_by=self.user,
        )
        recent_recovery = StreamRun.objects.create(
            stream_id="pending-recovery-existing",
            protocol_link=stale_run.protocol_link,
            callback_url="https://example.com/callback",
            status=StreamRun.Status.PENDING,
            created_by=self.user,
        )
        response = self.client.get(reverse("online-results-live-state"), data={"stream_id": stale_run.stream_id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(payload["resolved_stream_id"], recent_recovery.stream_id)
        launch_mock.assert_not_called()

    @override_settings(ONLINE_RESULTS_STREAM_RESUME_STALE_SEC=0)
    @patch("api.views._probe_online_results_stream_state", return_value={"ok": True, "found": False})
    @patch("api.views.launch_online_results_stream")
    def test_live_state_does_not_recover_when_stream_stopped_manually(self, launch_mock, _probe_mock):
        run = StreamRun.objects.create(
            stream_id="manual-stopped-stream",
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
            status=StreamRun.Status.STOPPED,
            last_error="manual_stop_by_user_1",
            created_by=self.user,
        )
        response = self.client.get(reverse("online-results-live-state"), data={"stream_id": run.stream_id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(payload["resolved_stream_id"], run.stream_id)
        launch_mock.assert_not_called()

    @override_settings(ONLINE_RESULTS_STREAM_RESUME_STALE_SEC=0)
    @patch("api.views._probe_online_results_stream_state", return_value={"ok": True, "found": True, "status": "running"})
    @patch("api.views.launch_online_results_stream")
    def test_live_state_does_not_recover_when_remote_stream_is_running(self, launch_mock, _probe_mock):
        run = StreamRun.objects.create(
            stream_id="healthy-stream-id",
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
            status=StreamRun.Status.RUNNING,
            started_at=timezone.now() - timedelta(minutes=20),
            created_by=self.user,
            launch_payload_json={"poll_interval_sec": 2.0},
        )
        response = self.client.get(reverse("online-results-live-state"), data={"stream_id": run.stream_id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(payload["resolved_stream_id"], run.stream_id)
        self.assertEqual(StreamRun.objects.filter(protocol_link=run.protocol_link).count(), 1)
        launch_mock.assert_not_called()

    def test_public_live_state_returns_payload_without_auth(self):
        run = StreamRun.objects.create(
            stream_id="stream-public-live-state",
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
            external_response_json={"stream_output": {"overall_stats_lines": ["x"]}},
        )
        access = PublicStreamAccess.objects.create(
            stream_run=run,
            token="public-token-1",
            expires_at=timezone.now() + timedelta(days=2),
            is_active=True,
        )
        self.client.logout()
        response = self.client.get(
            reverse("online-results-live-public-state", kwargs={"token": access.token}),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data["stream"]["stream_id"], run.stream_id)

    @override_settings(ONLINE_RESULTS_STREAM_RESUME_STALE_SEC=0)
    @patch("api.views._probe_online_results_stream_state", return_value={"ok": True, "found": False})
    @patch("api.views.launch_online_results_stream")
    def test_public_live_state_recovers_and_rebinds_access_link(self, launch_mock, _probe_mock):
        run = StreamRun.objects.create(
            stream_id="public-stale-stream",
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
            status=StreamRun.Status.RUNNING,
            started_at=timezone.now() - timedelta(minutes=10),
            external_response_json={"stream_output": {"overall_stats_lines": ["x"]}},
        )
        access = PublicStreamAccess.objects.create(
            stream_run=run,
            token="public-token-recover",
            expires_at=timezone.now() + timedelta(days=2),
            is_active=True,
        )

        def _launch_side_effect(run_id):
            new_run = StreamRun.objects.get(id=run_id)
            new_run.stream_id = "public-recovered-stream"
            new_run.status = StreamRun.Status.RUNNING
            new_run.save(update_fields=["stream_id", "status", "updated_at"])

        launch_mock.side_effect = _launch_side_effect
        self.client.logout()
        response = self.client.get(
            reverse("online-results-live-public-state", kwargs={"token": access.token}),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payload = response.json()
        self.assertEqual(payload["resolved_stream_id"], "public-recovered-stream")
        access.refresh_from_db()
        self.assertEqual(access.stream_run.stream_id, "public-recovered-stream")

    def test_public_live_page_hides_navigation_and_stream_id_block(self):
        run = StreamRun.objects.create(
            stream_id="stream-public-live-page",
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
            external_response_json={
                "stream_output": {
                    "last_tick": {
                        "updated_results": [
                            {
                                "athlete_key": "04.03.2026|ТЕСТ-ТЕСТ-ТЕСТ|Лист|Группа|1|ТЕСТ1",
                            }
                        ]
                    }
                }
            },
        )
        access = PublicStreamAccess.objects.create(
            stream_run=run,
            token="public-token-2",
            expires_at=timezone.now() + timedelta(days=2),
            is_active=True,
        )
        self.client.logout()
        response = self.client.get(reverse("online-results-live-public", kwargs={"token": access.token}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Онлайн протокол")
        self.assertContains(response, "ТЕСТ-ТЕСТ-ТЕСТ")
        self.assertNotContains(response, "Центр управления школой")
        self.assertNotContains(response, '<input type="text" class="form-control" name="stream_id"')

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
        self.assertEqual(PublicStreamAccess.objects.filter(stream_run=run, is_active=True).count(), 1)
        delay_mock.assert_called_once_with(run.id)

    @patch("api.views.launch_online_results_stream_task.delay")
    def test_stream_run_create_ignores_telegram_fields_on_launch(self, delay_mock):
        channel = TelegramChat.objects.create(
            chat_id=-1001234567890,
            type=TelegramChat.ChatType.CHANNEL,
            title="Live Results",
        )
        TelegramParticipant.objects.create(
            chat=channel,
            user_id=777000,
            is_bot=True,
            status=TelegramParticipant.MemberStatus.ADMIN,
        )
        payload = {
            "protocol_link": "https://docs.google.com/spreadsheets/d/test",
            "telegram_publish_enabled": "on",
            "telegram_channel": str(channel.id),
        }
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("online-results-stream-runs"), data=payload)

        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        run = StreamRun.objects.get()
        self.assertFalse(run.telegram_publish_enabled)
        self.assertIsNone(run.telegram_channel_id)
        delay_mock.assert_called_once_with(run.id)

    @patch("api.views.launch_online_results_stream_task.delay")
    def test_stream_run_reuses_existing_source_without_creating_duplicate(self, delay_mock):
        google_id = "1A2B3C4D5E6F7G8H9I0J1K2L3M"
        existing = StreamRun.objects.create(
            stream_id="existing-stream",
            protocol_link=f"https://docs.google.com/spreadsheets/d/{google_id}",
            callback_url="https://example.com/callback",
            status=StreamRun.Status.STOPPED,
        )
        payload = {"protocol_link": f"https://docs.google.com/spreadsheets/d/{google_id}/edit#gid=1"}
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("online-results-stream-runs"), data=payload)
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        self.assertEqual(StreamRun.objects.count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.status, StreamRun.Status.PENDING)
        delay_mock.assert_called_once_with(existing.id)

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

    @patch("api.views.enable_stream_telegram_publication")
    def test_stream_run_enable_telegram_from_row(self, enable_mock):
        channel = TelegramChat.objects.create(
            chat_id=-100777000111,
            type=TelegramChat.ChatType.CHANNEL,
            title="Live Results",
        )
        TelegramParticipant.objects.create(
            chat=channel,
            user_id=777001,
            is_bot=True,
            status=TelegramParticipant.MemberStatus.ADMIN,
        )
        run = StreamRun.objects.create(
            stream_id="stream-enable-tg",
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
            status=StreamRun.Status.RUNNING,
        )
        response = self.client.post(
            reverse("online-results-stream-runs"),
            data={
                "action": "telegram_enable",
                "run_id": str(run.id),
                "telegram_channel_id": str(channel.id),
            },
        )
        self.assertEqual(response.status_code, status.HTTP_302_FOUND)
        enable_mock.assert_called_once_with(run=run, channel_id=channel.id)

    def test_stream_runs_page_shows_competition_title_and_hides_service_columns(self):
        StreamRun.objects.create(
            stream_id="stream-title-case",
            protocol_link="https://docs.google.com/spreadsheets/d/test",
            callback_url="https://example.com/callback",
            external_response_json={"stream_output": {"competition_title": "Кубок Ленинградской области"}},
        )
        response = self.client.get(reverse("online-results-stream-runs"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertContains(response, "Кубок Ленинградской области")
        self.assertContains(response, "<th>Соревнование</th>", html=False)
        self.assertNotContains(response, "<th>Поток</th>", html=False)


@override_settings(
    ONLINE_RESULTS_STREAM_START_URL="http://localhost:8002/v1/streams",
    ONLINE_RESULTS_STREAM_TIMEOUT_SEC=15,
    ONLINE_RESULTS_STREAM_AUTH_TOKEN="",
    ONLINE_RESULTS_WEBHOOK_SECRET="test_secret",
    BOT_TOKEN="test-token",
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

    def test_group_table_updated_infers_completed_group_for_run1(self):
        run = self._create_run(stream_id="remote-infer-run1")
        event = WebhookEvent.objects.create(
            stream_id="remote-infer-run1",
            event_type="group_table_updated",
            payload_json={
                "payload": {
                    "group_key": "sheet|group-a",
                    "sheet_name": "sheet",
                    "group_name": "group-a",
                    "lines": ["line"],
                    "data": {
                        "rows": [
                            {"run1": "21.10", "run2": "-", "total": "21.10"},
                            {"run1": "22.20", "run2": "-", "total": "22.20"},
                        ]
                    },
                }
            },
            payload_hash="8" * 64,
        )
        process_online_results_webhook_event(event.id)
        run.refresh_from_db()
        completed = run.external_response_json.get("stream_output", {}).get("completed_groups", {})
        self.assertIn("sheet|group-a|run1", completed)
        self.assertEqual(completed.get("sheet|group-a|run1", {}).get("run_stage"), 1)

    def test_group_table_updated_does_not_infer_completed_run1_when_group_is_in_progress(self):
        run = self._create_run(stream_id="remote-infer-in-progress")
        event = WebhookEvent.objects.create(
            stream_id="remote-infer-in-progress",
            event_type="group_table_updated",
            payload_json={
                "payload": {
                    "group_key": "sheet|group-b",
                    "sheet_name": "sheet",
                    "group_name": "group-b",
                    "lines": ["line"],
                    "data": {
                        "rows": [
                            {"run1": "21.10", "run2": "-", "total": "21.10"},
                            {"run1": "-", "run2": "-", "total": "-"},
                        ]
                    },
                }
            },
            payload_hash="9" * 64,
        )
        process_online_results_webhook_event(event.id)
        run.refresh_from_db()
        completed = run.external_response_json.get("stream_output", {}).get("completed_groups", {})
        self.assertNotIn("sheet|group-b|run1", completed)

    def test_stream_snapshot_populates_teams_groups_and_completed(self):
        run = self._create_run(stream_id="remote-snapshot")
        event = WebhookEvent.objects.create(
            stream_id="remote-snapshot",
            event_type="stream_snapshot",
            payload_json={
                "payload": {
                    "competition_phase": "upcoming",
                    "status_text": "Соревнование скоро начнется",
                    "competition_title": "КУБОК ТЕСТ",
                    "teams": ["Канаев Ски Клаб", "ЛУЧ"],
                    "groups": [
                        {
                            "group_key": "sheet|group-c",
                            "sheet_name": "sheet",
                            "group_name": "group-c",
                            "run_stage": 1,
                            "is_finalized": True,
                            "data": {"headers": ["h1"], "rows": [{"run1": "21.11"}], "lines_plain": ["line"]},
                        }
                    ],
                }
            },
            payload_hash="a" * 63 + "b",
        )
        process_online_results_webhook_event(event.id)
        run.refresh_from_db()
        output = run.external_response_json.get("stream_output", {})
        self.assertEqual(output.get("competition_phase"), "upcoming")
        self.assertEqual(output.get("competition_title"), "КУБОК ТЕСТ")
        self.assertEqual(output.get("teams"), ["Канаев Ски Клаб", "ЛУЧ"])
        self.assertIn("sheet|group-c", output.get("latest_group_tables", {}))
        self.assertIn("sheet|group-c|run1", output.get("completed_groups", {}))

    def test_start_forecast_event_is_saved(self):
        run = self._create_run(stream_id="remote-forecast")
        event = WebhookEvent.objects.create(
            stream_id="remote-forecast",
            event_type="start_forecast_updated",
            payload_json={
                "payload": {
                    "competition_phase": "running",
                    "rows": [
                        {"athlete_key": "a1", "club": "Канаев Ски Клаб", "eta": "2026-03-07T12:00:00"},
                    ],
                }
            },
            payload_hash="b" * 63 + "c",
        )
        process_online_results_webhook_event(event.id)
        run.refresh_from_db()
        output = run.external_response_json.get("stream_output", {})
        self.assertEqual(output.get("competition_phase"), "running")
        self.assertEqual(output.get("start_forecast", {}).get("rows", [])[0].get("athlete_key"), "a1")

    @patch("api.telegram_streaming._send_message")
    def test_telegram_publication_sends_message_for_new_group(self, send_mock):
        channel = TelegramChat.objects.create(
            chat_id=-100222000111,
            type=TelegramChat.ChatType.CHANNEL,
            title="Result Channel",
        )
        send_mock.side_effect = [
            SimpleNamespace(message_id=501),  # table
            SimpleNamespace(message_id=502),  # finisher placeholder
            SimpleNamespace(message_id=503),  # link
        ]
        run = self._create_run(stream_id="remote-tg-send")
        run.telegram_publish_enabled = True
        run.telegram_channel = channel
        run.save(update_fields=["telegram_publish_enabled", "telegram_channel", "updated_at"])
        PublicStreamAccess.objects.create(
            stream_run=run,
            token="tg-send-public-token",
            expires_at=timezone.now() + timedelta(days=1),
            is_active=True,
        )

        event = WebhookEvent.objects.create(
            stream_id="remote-tg-send",
            event_type="group_table_updated",
            payload_json={
                "payload": {
                    "group_key": "sheet|group-1",
                    "group_name": "group-1",
                    "data": {
                        "rows": [
                            {
                                "place": 1,
                                "start_number": 12,
                                "full_name": "Иванов Иван",
                                "run1": "20.11",
                                "run2": "-",
                                "total": "20.11",
                                "interval": "+0.00",
                            }
                        ]
                    },
                }
            },
            payload_hash="c" * 63 + "d",
        )
        process_online_results_webhook_event(event.id)

        run.refresh_from_db()
        self.assertEqual(run.telegram_active_message_id, 501)
        self.assertEqual(run.telegram_active_group_key, "sheet|group-1")
        self.assertEqual(run.telegram_active_run_stage, 1)
        self.assertEqual(run.telegram_finisher_message_id, 502)
        self.assertEqual(run.telegram_link_message_id, 503)
        self.assertEqual(send_mock.call_count, 3)

    @patch("api.telegram_streaming._send_message")
    def test_telegram_publication_creates_finisher_post_from_result_updated(self, send_mock):
        channel = TelegramChat.objects.create(
            chat_id=-100444000111,
            type=TelegramChat.ChatType.CHANNEL,
            title="Result Channel",
        )
        send_mock.side_effect = [
            SimpleNamespace(message_id=801),  # finisher
            SimpleNamespace(message_id=802),  # link
        ]
        run = self._create_run(stream_id="remote-tg-finisher")
        run.telegram_publish_enabled = True
        run.telegram_channel = channel
        run.external_response_json = {
            "stream_output": {
                "latest_group_tables": {
                    "sheet|group-z": {
                        "data": {
                            "rows": [
                                {"athlete_key": "a-key-1", "place": 4},
                            ]
                        }
                    }
                }
            }
        }
        run.save(
            update_fields=[
                "telegram_publish_enabled",
                "telegram_channel",
                "external_response_json",
                "updated_at",
            ]
        )
        PublicStreamAccess.objects.create(
            stream_run=run,
            token="tg-finisher-public-token",
            expires_at=timezone.now() + timedelta(days=1),
            is_active=True,
        )

        event = WebhookEvent.objects.create(
            stream_id="remote-tg-finisher",
            event_type="result_updated",
            payload_json={
                "payload": {
                    "data": {
                        "updated_results": [
                            {
                                "athlete_key": "a-key-1",
                                "full_name": "Иванов Иван Иванович",
                                "club": "Канаев",
                                "run1": "21.11",
                                "run2": "-",
                                "total": "21.11",
                            }
                        ]
                    }
                }
            },
            payload_hash="f" * 63 + "1",
        )
        process_online_results_webhook_event(event.id)

        run.refresh_from_db()
        self.assertEqual(run.telegram_finisher_message_id, 801)
        self.assertEqual(run.telegram_link_message_id, 802)
        self.assertTrue(run.telegram_finisher_last_hash)
        self.assertEqual(send_mock.call_count, 2)

    @patch("api.telegram_streaming._delete_message")
    @patch("api.telegram_streaming._send_message")
    def test_telegram_publication_resets_tail_messages_after_group_completed(self, send_mock, delete_mock):
        channel = TelegramChat.objects.create(
            chat_id=-100555000111,
            type=TelegramChat.ChatType.CHANNEL,
            title="Result Channel",
        )
        send_mock.side_effect = [
            SimpleNamespace(message_id=901),  # table
            SimpleNamespace(message_id=902),  # finisher
            SimpleNamespace(message_id=903),  # link
        ]
        run = self._create_run(stream_id="remote-tg-last-post")
        run.telegram_publish_enabled = True
        run.telegram_channel = channel
        run.external_response_json = {
            "stream_output": {
                "latest_group_tables": {
                    "sheet|group-a": {
                        "data": {"rows": [{"athlete_key": "ax", "place": 2}]}
                    }
                }
            }
        }
        run.save(
            update_fields=[
                "telegram_publish_enabled",
                "telegram_channel",
                "external_response_json",
                "updated_at",
            ]
        )
        PublicStreamAccess.objects.create(
            stream_run=run,
            token="tg-last-post-public-token",
            expires_at=timezone.now() + timedelta(days=1),
            is_active=True,
        )

        group_event = WebhookEvent.objects.create(
            stream_id="remote-tg-last-post",
            event_type="group_table_updated",
            payload_json={
                "payload": {
                    "group_key": "sheet|group-a",
                    "group_name": "group-a",
                    "data": {
                        "rows": [
                            {"athlete_key": "ax", "place": 1, "start_number": 7, "full_name": "Петров Петр", "run1": "20.10", "run2": "-", "total": "20.10", "interval": "+0.00"}
                        ]
                    },
                }
            },
            payload_hash="f" * 63 + "2",
        )
        process_online_results_webhook_event(group_event.id)

        completed_event = WebhookEvent.objects.create(
            stream_id="remote-tg-last-post",
            event_type="group_completed",
            payload_json={
                "payload": {
                    "group_key": "sheet|group-a",
                    "group_name": "group-a",
                    "data": {
                        "rows": [
                            {"athlete_key": "ax", "place": 1, "start_number": 7, "full_name": "Петров Петр", "run1": "20.10", "run2": "-", "total": "20.10", "interval": "+0.00"}
                        ]
                    },
                }
            },
            payload_hash="f" * 63 + "3",
        )
        process_online_results_webhook_event(completed_event.id)

        run.refresh_from_db()
        self.assertIsNone(run.telegram_active_message_id)
        self.assertIsNone(run.telegram_finisher_message_id)
        self.assertIsNone(run.telegram_link_message_id)
        self.assertEqual(delete_mock.call_count, 2)
        self.assertEqual(send_mock.call_count, 3)

    @patch("api.telegram_streaming._send_message")
    @patch("api.telegram_streaming._edit_message")
    def test_telegram_publication_edits_active_message_for_same_group(self, edit_mock, send_mock):
        channel = TelegramChat.objects.create(
            chat_id=-100333000111,
            type=TelegramChat.ChatType.CHANNEL,
            title="Result Channel",
        )
        send_mock.return_value = SimpleNamespace(message_id=700)
        run = self._create_run(stream_id="remote-tg-edit")
        run.telegram_publish_enabled = True
        run.telegram_channel = channel
        run.save(update_fields=["telegram_publish_enabled", "telegram_channel", "updated_at"])
        PublicStreamAccess.objects.create(
            stream_run=run,
            token="tg-edit-public-token",
            expires_at=timezone.now() + timedelta(days=1),
            is_active=True,
        )

        first_event = WebhookEvent.objects.create(
            stream_id="remote-tg-edit",
            event_type="group_table_updated",
            payload_json={
                "payload": {
                    "group_key": "sheet|group-x",
                    "group_name": "group-x",
                    "data": {"rows": [{"place": 1, "start_number": 11, "full_name": "Петров Петр", "run1": "22.11", "run2": "-", "total": "22.11", "interval": "+0.00"}]},
                }
            },
            payload_hash="d" * 63 + "e",
        )
        process_online_results_webhook_event(first_event.id)
        run.refresh_from_db()

        second_event = WebhookEvent.objects.create(
            stream_id="remote-tg-edit",
            event_type="group_table_updated",
            payload_json={
                "payload": {
                    "group_key": "sheet|group-x",
                    "group_name": "group-x",
                    "data": {"rows": [{"place": 1, "start_number": 11, "full_name": "Петров Петр", "run1": "21.95", "run2": "-", "total": "21.95", "interval": "+0.00"}]},
                }
            },
            payload_hash="e" * 63 + "f",
        )
        process_online_results_webhook_event(second_event.id)
        run.refresh_from_db()

        self.assertEqual(run.telegram_active_message_id, 700)
        self.assertEqual(run.telegram_active_group_key, "sheet|group-x")
        self.assertEqual(run.telegram_active_run_stage, 1)
        self.assertEqual(send_mock.call_count, 3)
        edit_mock.assert_called_once()

    @patch("api.telegram_streaming._delete_message")
    @patch("api.telegram_streaming._send_message")
    def test_telegram_publication_switch_group_rotates_tail_posts(self, send_mock, delete_mock):
        channel = TelegramChat.objects.create(
            chat_id=-100666000111,
            type=TelegramChat.ChatType.CHANNEL,
            title="Result Channel",
        )
        send_mock.side_effect = [
            SimpleNamespace(message_id=1001),  # first table
            SimpleNamespace(message_id=1002),  # first finisher
            SimpleNamespace(message_id=1003),  # first link
            SimpleNamespace(message_id=1004),  # second table
            SimpleNamespace(message_id=1005),  # second finisher
            SimpleNamespace(message_id=1006),  # second link
        ]
        run = self._create_run(stream_id="remote-tg-switch-group")
        run.telegram_publish_enabled = True
        run.telegram_channel = channel
        run.save(update_fields=["telegram_publish_enabled", "telegram_channel", "updated_at"])
        PublicStreamAccess.objects.create(
            stream_run=run,
            token="tg-switch-group-token",
            expires_at=timezone.now() + timedelta(days=1),
            is_active=True,
        )

        first_event = WebhookEvent.objects.create(
            stream_id="remote-tg-switch-group",
            event_type="group_table_updated",
            payload_json={
                "payload": {
                    "group_key": "sheet|group-a",
                    "group_name": "group-a",
                    "data": {
                        "rows": [
                            {"place": 1, "start_number": 1, "full_name": "Петров Петр", "run1": "20.10", "run2": "-", "total": "20.10", "interval": "+0.00"}
                        ]
                    },
                }
            },
            payload_hash="a" * 63 + "1",
        )
        process_online_results_webhook_event(first_event.id)
        run.refresh_from_db()
        self.assertEqual(run.telegram_active_message_id, 1001)
        self.assertEqual(run.telegram_finisher_message_id, 1002)
        self.assertEqual(run.telegram_link_message_id, 1003)

        second_event = WebhookEvent.objects.create(
            stream_id="remote-tg-switch-group",
            event_type="group_table_updated",
            payload_json={
                "payload": {
                    "group_key": "sheet|group-b",
                    "group_name": "group-b",
                    "data": {
                        "rows": [
                            {"place": 1, "start_number": 2, "full_name": "Иванов Иван", "run1": "21.10", "run2": "-", "total": "21.10", "interval": "+0.00"}
                        ]
                    },
                }
            },
            payload_hash="a" * 63 + "2",
        )
        process_online_results_webhook_event(second_event.id)
        run.refresh_from_db()

        self.assertEqual(run.telegram_active_message_id, 1004)
        self.assertEqual(run.telegram_active_group_key, "sheet|group-b")
        self.assertEqual(run.telegram_finisher_message_id, 1005)
        self.assertEqual(run.telegram_link_message_id, 1006)
        self.assertEqual(delete_mock.call_count, 2)
        self.assertEqual(send_mock.call_count, 6)

    @patch("api.telegram_streaming._publish_bootstrap_state_for_run_id")
    @patch("api.telegram_streaming._delete_message_safe")
    def test_enable_telegram_switch_channel_cleans_previous_tail_posts(self, delete_safe_mock, bootstrap_mock):
        old_channel = TelegramChat.objects.create(
            chat_id=-100777000111,
            type=TelegramChat.ChatType.CHANNEL,
            title="Old Result Channel",
        )
        new_channel = TelegramChat.objects.create(
            chat_id=-100888000111,
            type=TelegramChat.ChatType.CHANNEL,
            title="New Result Channel",
        )
        run = self._create_run(stream_id="remote-tg-switch-channel")
        run.telegram_publish_enabled = True
        run.telegram_channel = old_channel
        run.telegram_active_message_id = 2001
        run.telegram_finisher_message_id = 2002
        run.telegram_link_message_id = 2003
        run.save(
            update_fields=[
                "telegram_publish_enabled",
                "telegram_channel",
                "telegram_active_message_id",
                "telegram_finisher_message_id",
                "telegram_link_message_id",
                "updated_at",
            ]
        )

        with self.captureOnCommitCallbacks(execute=True):
            enable_stream_telegram_publication(run=run, channel_id=new_channel.id)
        run.refresh_from_db()

        self.assertTrue(run.telegram_publish_enabled)
        self.assertEqual(run.telegram_channel_id, new_channel.id)
        self.assertIsNone(run.telegram_active_message_id)
        self.assertIsNone(run.telegram_finisher_message_id)
        self.assertIsNone(run.telegram_link_message_id)
        self.assertEqual(delete_safe_mock.call_count, 2)
        deleted_ids = sorted(call.kwargs["message_id"] for call in delete_safe_mock.call_args_list)
        self.assertEqual(deleted_ids, [2002, 2003])
        bootstrap_mock.assert_called_once_with(run.id)

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
