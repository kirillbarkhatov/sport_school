import os
import socket

import pytest
from django.contrib.auth.models import Group as AuthGroup

from users.constants import ADMIN_GROUP_NAME, COACH_GROUP_NAME, MANAGER_GROUP_NAME
from users.models import User

from .factories import (
    AthleteFactory,
    ClassFactory,
    CompetitionFactory,
    FamilyFactory,
    FamilyServiceFactory,
    GroupFactory,
    NotificationFactory,
    PersonFactory,
)

_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "::1", os.getenv("POSTGRES_HOST", "localhost")}
_real_connect = socket.socket.connect


def _guarded_connect(sock, address):
    """Тесты не должны ходить в интернет (Telegram, S3, AI): разрешены только БД и localhost."""
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        host = address[0]
        allowed_ips = set()
        for name in _ALLOWED_HOSTS:
            try:
                allowed_ips.update(info[4][0] for info in socket.getaddrinfo(name, None))
            except OSError:
                pass
        if host not in _ALLOWED_HOSTS and host not in allowed_ips:
            raise OSError(f"Сетевой доступ из тестов запрещён: {address}")
    return _real_connect(sock, address)


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)


ROLES = ("anonymous", "pending", "approved", "coach", "manager", "admin", "superuser")


def _make_user(role: str) -> User | None:
    if role == "anonymous":
        return None
    # У User нет username (логин по email), поэтому create, а не create_user
    user = User.objects.create(email=f"{role}@example.com", is_approved=role != "pending")
    group_name = {"coach": COACH_GROUP_NAME, "manager": MANAGER_GROUP_NAME, "admin": ADMIN_GROUP_NAME}.get(role)
    if group_name:
        user.groups.add(AuthGroup.objects.get_or_create(name=group_name)[0])
    if role == "superuser":
        user.is_staff = True
        user.is_superuser = True
        user.save(update_fields=["is_staff", "is_superuser"])
    return user


@pytest.fixture
def client_for(client, db):
    def _client(role: str):
        user = _make_user(role)
        if user is not None:
            client.force_login(user)
        return client

    return _client


@pytest.fixture
def sample_data(db):
    """Минимальный набор связанных объектов для детальных страниц."""
    family = FamilyFactory()
    athlete = AthleteFactory()
    return {
        "person": PersonFactory(),
        "athlete": athlete,
        "family": family,
        "service": FamilyServiceFactory(family=family),
        "group": GroupFactory(),
        "class": ClassFactory(),
        "competition": CompetitionFactory(),
        "notification": NotificationFactory(),
    }
