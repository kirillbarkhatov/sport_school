"""Проверка матрицы доступа (tests/access_matrix.py). Обязательные ворота релизов этапа 4."""

import warnings

import pytest
from django.test import Client
from django.urls import reverse

from .access_matrix import EXPECTED, KNOWN_HOLES, ROLES
from .conftest import _make_user
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
from .test_smoke import DETAIL_PAGES, STATIC_PAGES

pytestmark = pytest.mark.django_db
DETAIL_KWARGS = dict(DETAIL_PAGES)


def _kwargs(name, data):
    return DETAIL_KWARGS[name](data) if name in DETAIL_KWARGS else {}


@pytest.fixture(params=["legacy", "memberships"])
def world(request, settings):
    family = FamilyFactory()
    data = {
        "person": PersonFactory(),
        "athlete": AthleteFactory(),
        "family": family,
        "service": FamilyServiceFactory(family=family),
        "group": GroupFactory(),
        "class": ClassFactory(),
        "competition": CompetitionFactory(),
        "notification": NotificationFactory(),
    }
    settings.ORGS_ROLES_SOURCE = "legacy"
    users = {role: _make_user(role) for role in ROLES}
    if request.param == "memberships":
        # Ровно как при переключении на проде: перенос из старых полей, затем смена источника ролей.
        from organizations.legacy import import_legacy

        import_legacy()
        settings.ORGS_ROLES_SOURCE = "memberships"
    return data, users


def test_matrix_covers_every_smoke_page():
    pages = {f"GET {name}" for name in STATIC_PAGES} | {f"GET {name}" for name, _ in DETAIL_PAGES}
    missing = pages - set(EXPECTED)
    assert not missing, f"Страницы без строки в матрице доступа: {sorted(missing)}"


def test_known_holes_refer_to_matrix_rows():
    assert set(KNOWN_HOLES) <= set(EXPECTED)


@pytest.mark.parametrize("key", sorted(EXPECTED))
def test_access_matches_matrix(world, key):
    data, users = world
    method, name = key.split(" ", 1)
    actual = {}
    for role in ROLES:
        client = Client()
        if users[role] is not None:
            client.force_login(users[role])
        url = reverse(name, kwargs=_kwargs(name, data))
        response = client.get(url) if method == "GET" else client.post(url, {})
        actual[role] = response.status_code
    expected = dict(zip(ROLES, EXPECTED[key], strict=True))
    assert actual == expected, f"{key}: доступ изменился. Было {expected}, стало {actual}"
    if key in KNOWN_HOLES:
        warnings.warn(f"KNOWN_HOLE {key}: сейчас {expected}, должно быть {KNOWN_HOLES[key]}", stacklevel=1)
