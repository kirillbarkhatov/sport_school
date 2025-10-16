from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

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
