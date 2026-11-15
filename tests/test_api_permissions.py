"""Регрессия: API пользователей/людей не должно быть доступно обычным пользователям и давать повышение прав."""

import base64

import pytest
from django.test import override_settings

from users.models import User

from .factories import PersonFactory

pytestmark = pytest.mark.django_db


def _user(email, **flags):
    return User.objects.create(email=email, **flags)


@pytest.mark.parametrize("approved", [False, True])
def test_regular_user_cannot_read_or_escalate_via_users_api(client, approved):
    user = _user("parent@example.com", is_approved=approved)
    client.force_login(user)
    assert client.get("/api/user/").status_code == 403
    response = client.patch(
        f"/api/user/{user.pk}/", {"is_superuser": True, "is_staff": True}, content_type="application/json"
    )
    assert response.status_code == 403
    user.refresh_from_db()
    assert not user.is_superuser and not user.is_staff


def test_regular_user_cannot_touch_persons_api(client):
    person = PersonFactory()
    client.force_login(_user("parent@example.com", is_approved=True))
    assert client.get("/api/person/").status_code == 403
    assert client.delete(f"/api/person/{person.pk}/").status_code == 403


def test_staff_sees_users_without_secrets_and_cannot_grant_roles(client):
    staff = _user("staff@example.com", is_staff=True, is_approved=True)
    target = _user("other@example.com", is_approved=True, token="secret-token")
    target.set_password("long-password-123")
    target.save()
    client.force_login(staff)
    rows = client.get("/api/user/").json()
    assert rows and all("password" not in row and "token" not in row for row in rows)
    client.patch(f"/api/user/{target.pk}/", {"is_superuser": True, "is_staff": True}, content_type="application/json")
    target.refresh_from_db()
    assert not target.is_superuser and not target.is_staff


def test_staff_can_create_user_with_hashed_password(client):
    client.force_login(_user("staff@example.com", is_staff=True))
    response = client.post(
        "/api/user/", {"email": "new@example.com", "password": "long-password-123"}, content_type="application/json"
    )
    assert response.status_code == 201
    created = User.objects.get(email="new@example.com")
    assert created.check_password("long-password-123") and "password" not in response.json()


def test_basic_auth_is_disabled(client):
    admin = _user("admin@example.com", is_staff=True, is_superuser=True)
    admin.set_password("long-password-123")
    admin.save()
    credentials = base64.b64encode(b"admin@example.com:long-password-123").decode()
    response = client.get("/api/user/", HTTP_AUTHORIZATION=f"Basic {credentials}")
    assert response.status_code in (401, 403)


def test_unapproved_user_blocked_from_assistant_api(client):
    client.force_login(_user("pending@example.com", is_approved=False))
    assert client.get("/api/assistant/upcoming-trainings/").status_code == 403


@override_settings(WHATSAPP_SYNC_TOKEN="sync-secret")
def test_whatsapp_sync_requires_token_when_configured(client):
    url = "/api/whatsapp_chat_members/"
    assert client.post(url, {}, content_type="application/json").status_code == 403
    wrong = client.post(url, {}, content_type="application/json", HTTP_X_SYNC_TOKEN="nope")
    assert wrong.status_code == 403
    right = client.post(url, {}, content_type="application/json", HTTP_X_SYNC_TOKEN="sync-secret")
    assert right.status_code == 400  # токен принят, дальше обычная валидация тела
