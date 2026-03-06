import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import StreamRun, WebhookEvent
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
